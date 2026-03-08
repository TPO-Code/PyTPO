from __future__ import annotations

import json
import os
import sys
import tempfile

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


DEBUGGER_HARNESS_CODE = r'''
import bdb
import importlib.util
import json
import os
import runpy
import sys
import traceback

PROTO_PREFIX = "__DBG__:"


def send_event(event, data=None):
    msg = {"type": "event", "event": event, "data": data}
    sys.stderr.write(PROTO_PREFIX + json.dumps(msg) + "\n")
    sys.stderr.flush()


class RemoteDebugger(bdb.Bdb):
    def __init__(self):
        super().__init__()
        self.mainpyfile = None
        self.paused_frame = None
        self.project_roots = [
            self.canonic(path)
            for path in json.loads(os.environ.get("PYTPO_DEBUG_PROJECT_ROOTS", "[]") or "[]")
            if str(path or "").strip()
        ]
        self.breakpoint_specs = {}
        self.breakpoint_hits = {}
        self.watch_expressions = []
        self.last_resume_action = "continue"

    def user_line(self, frame):
        filename = self.canonic(frame.f_code.co_filename)
        line_number = int(frame.f_lineno)
        has_breakpoint = self._has_breakpoint(filename, line_number)
        if not has_breakpoint and self.last_resume_action == "continue":
            self._resume_skipping_frame()
            return
        if not has_breakpoint and not self._is_user_file(filename):
            self._resume_skipping_frame()
            return
        if has_breakpoint and not self._breakpoint_should_pause(frame, filename, line_number):
            self._resume_skipping_frame()
            return

        self.paused_frame = frame
        send_event(
            "stop",
            {
                "file": filename,
                "line": line_number,
                "function": frame.f_code.co_name,
                "locals": self.get_safe_dict(frame.f_locals),
                "globals": self.get_safe_globals(frame.f_globals),
                "stack": self.get_stack_data(frame),
                "watches": self.current_watches_payload(),
            },
        )
        self.wait_for_command()

    def user_exception(self, frame, exc_info):
        exc_type, exc_value, exc_tb = exc_info
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        send_event(
            "exception",
            {
                "file": self.canonic(frame.f_code.co_filename),
                "line": frame.f_lineno,
                "type": getattr(exc_type, "__name__", str(exc_type)),
                "message": str(exc_value),
                "traceback": tb_text,
            },
        )

    def get_safe_dict(self, values):
        safe = {}
        for key, value in values.items():
            try:
                safe[str(key)] = repr(value)
            except Exception:
                safe[str(key)] = "<repr-error>"
        return safe

    def get_safe_globals(self, values):
        safe = {}
        for key, value in values.items():
            if str(key).startswith("__") and str(key).endswith("__"):
                continue
            try:
                safe[str(key)] = repr(value)
            except Exception:
                safe[str(key)] = "<repr-error>"
        return safe

    def get_stack_data(self, frame):
        frames = []
        current = frame
        while current is not None:
            frames.append(
                {
                    "file": self.canonic(current.f_code.co_filename),
                    "line": current.f_lineno,
                    "function": current.f_code.co_name,
                    "locals": self.get_safe_dict(current.f_locals),
                    "globals": self.get_safe_globals(current.f_globals),
                }
            )
            current = current.f_back
        frames.reverse()
        return frames

    def _is_library_path(self, filename):
        path = str(filename or "")
        if not path:
            return False
        lowered = path.lower()
        if "site-packages" in lowered or "dist-packages" in lowered:
            return True
        prefixes = [sys.prefix, getattr(sys, "base_prefix", sys.prefix), getattr(sys, "exec_prefix", sys.prefix)]
        for prefix in prefixes:
            root = self.canonic(prefix)
            if root and path.startswith(root):
                return True
        return False

    def _is_user_file(self, filename):
        path = str(filename or "")
        if not path:
            return False
        if path in self.breakpoint_specs:
            return True
        if self.mainpyfile and path == self.mainpyfile:
            return True
        for root in self.project_roots:
            if root and path.startswith(root):
                return not self._is_library_path(path)
        return False

    def _has_breakpoint(self, filename, line_number):
        specs = self.breakpoint_specs.get(filename, {})
        return int(line_number) in specs

    def _breakpoint_should_pause(self, frame, filename, line_number):
        spec = dict(self.breakpoint_specs.get(filename, {}).get(int(line_number)) or {})
        key = (filename, int(line_number))
        hit_count = int(self.breakpoint_hits.get(key, 0)) + 1
        self.breakpoint_hits[key] = hit_count

        condition = str(spec.get("condition") or "").strip()
        if condition:
            result = self._evaluate_expression(condition, frame)
            if result.get("status") != "ok":
                send_event(
                    "output",
                    {"text": f"[breakpoint] condition error at {filename}:{line_number}: {result.get('error') or ''}"},
                )
                return False
            if not bool(result.get("raw_value")):
                return False

        target_hits = max(0, int(spec.get("hit_count") or 0))
        if target_hits > 0 and hit_count < target_hits:
            return False

        log_message = str(spec.get("log_message") or "").strip()
        if log_message:
            send_event(
                "output",
                {"text": f"[logpoint] {filename}:{line_number}: {self._format_log_message(log_message, frame)}"},
            )
            return False
        return True

    def _format_log_message(self, message, frame):
        text = str(message or "")
        try:
            return text.format(**frame.f_locals)
        except Exception:
            return text

    def _evaluate_expression(self, expression, frame=None):
        expr = str(expression or "").strip()
        active_frame = frame or self.paused_frame
        if not expr:
            return {"expression": expr, "status": "error", "error": "Missing expression"}
        if active_frame is None:
            return {"expression": expr, "status": "error", "error": "Program is not paused"}
        try:
            value = eval(expr, active_frame.f_globals, active_frame.f_locals)
            rendered = repr(value)
            return {
                "expression": expr,
                "status": "ok",
                "value": rendered,
                "raw_value": value,
            }
        except Exception as exc:
            return {
                "expression": expr,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def current_watches_payload(self):
        return [
            {
                key: value
                for key, value in self._evaluate_expression(expression).items()
                if key != "raw_value"
            }
            for expression in self.watch_expressions
        ]

    def _set_breakpoint_specs(self, mapping):
        old_files = set(self.breakpoint_specs)
        new_specs = {}
        new_files = set()
        for file_path, values in (mapping or {}).items():
            canonical = self.canonic(file_path)
            if not canonical:
                continue
            file_specs = {}
            if isinstance(values, list):
                for raw in values:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        line = int(raw.get("line") or 0)
                    except Exception:
                        continue
                    if line <= 0:
                        continue
                    file_specs[line] = {
                        "condition": str(raw.get("condition") or "").strip(),
                        "hit_count": max(0, int(raw.get("hit_count") or 0)),
                        "log_message": str(raw.get("log_message") or "").strip(),
                    }
            if file_specs:
                new_specs[canonical] = file_specs
                new_files.add(canonical)

        for file_path in old_files | new_files:
            try:
                self.clear_all_file_breaks(file_path)
            except Exception:
                pass

        for file_path, values in new_specs.items():
            for line, spec in values.items():
                try:
                    condition = str(spec.get("condition") or "").strip() or None
                    self.set_break(file_path, int(line), cond=condition)
                except Exception:
                    pass

        self.breakpoint_specs = new_specs
        self.breakpoint_hits = {
            key: value
            for key, value in self.breakpoint_hits.items()
            if key[0] in self.breakpoint_specs and key[1] in self.breakpoint_specs[key[0]]
        }
        send_event("breakpoints_set", {"files": sorted(self.breakpoint_specs)})

    def _resume_skipping_frame(self):
        action = str(self.last_resume_action or "continue")
        if action == "next" and self.paused_frame is not None:
            self.set_next(self.paused_frame)
            return
        if action == "step":
            self.set_step()
            return
        self.set_continue()

    def wait_for_command(self):
        while True:
            line = sys.stdin.readline()
            if not line:
                return
            try:
                cmd = json.loads(line)
            except Exception:
                continue

            action = cmd.get("action")
            if action == "step":
                self.last_resume_action = "step"
                self.set_step()
                return
            if action == "next":
                self.last_resume_action = "next"
                if self.paused_frame is not None:
                    self.set_next(self.paused_frame)
                else:
                    self.set_step()
                return
            if action == "continue":
                self.last_resume_action = "continue"
                self.set_continue()
                return
            if action == "quit":
                raise SystemExit(0)
            if action == "set_breakpoints":
                self._set_breakpoint_specs(cmd.get("breakpoints") or {})
                continue
            if action == "set_watches":
                self.watch_expressions = [
                    str(expr or "").strip() for expr in (cmd.get("expressions") or []) if str(expr or "").strip()
                ]
                send_event("watch_values", {"watches": self.current_watches_payload()})
                continue
            if action == "evaluate":
                result = self._evaluate_expression(cmd.get("expression") or "")
                result.pop("raw_value", None)
                send_event("evaluation_result", result)

    def wait_for_initial_setup(self):
        while True:
            line = sys.stdin.readline()
            if not line:
                return "continue"
            try:
                cmd = json.loads(line)
            except Exception:
                continue
            action = cmd.get("action")
            if action == "set_breakpoints":
                self._set_breakpoint_specs(cmd.get("breakpoints") or {})
                continue
            if action == "set_watches":
                self.watch_expressions = [
                    str(expr or "").strip() for expr in (cmd.get("expressions") or []) if str(expr or "").strip()
                ]
                continue
            if action == "evaluate":
                result = self._evaluate_expression(cmd.get("expression") or "")
                result.pop("raw_value", None)
                send_event("evaluation_result", result)
                continue
            if action == "quit":
                raise SystemExit(0)
            if action in {"continue", "step", "next"}:
                self.last_resume_action = str(action)
                return str(action)

    def run_script(self, filename):
        self.mainpyfile = self.canonic(filename)
        script_dir = os.path.dirname(os.path.abspath(filename))
        if script_dir and script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        with open(filename, "rb") as handle:
            source = handle.read()

        code = compile(source, filename, "exec")
        globals_dict = {
            "__name__": "__main__",
            "__file__": filename,
            "__package__": None,
            "__cached__": None,
        }
        self.runctx(code, globals_dict, globals_dict)

    @staticmethod
    def resolve_module_entry(module_name):
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            raise ModuleNotFoundError(module_name)
        if spec.submodule_search_locations:
            main_name = module_name + ".__main__"
            main_spec = importlib.util.find_spec(main_name)
            if main_spec is None or not main_spec.origin:
                raise ModuleNotFoundError(main_name)
            return main_spec
        if not spec.origin:
            raise ModuleNotFoundError(module_name)
        return spec

    def run_module(self, module_name, argv):
        entry_spec = self.resolve_module_entry(module_name)
        self.mainpyfile = self.canonic(entry_spec.origin)
        cwd = os.getcwd()
        if cwd and cwd not in sys.path:
            sys.path.insert(0, cwd)

        original_argv = sys.argv[:]
        try:
            sys.argv = [entry_spec.origin, *argv]
            self.runctx(
                "runpy.run_module(module_name, run_name='__main__', alter_sys=True)",
                {"runpy": runpy, "module_name": module_name},
                {},
            )
        finally:
            sys.argv = original_argv


def main():
    if len(sys.argv) < 3:
        send_event("fatal", {"message": "Missing launch mode or target", "traceback": ""})
        raise SystemExit(1)

    launch_mode = sys.argv[1]
    launch_target = sys.argv[2]
    launch_args = sys.argv[3:]
    debugger = RemoteDebugger()

    try:
        if launch_mode == "module":
            entry_spec = debugger.resolve_module_entry(launch_target)
            send_event("started", {"file": debugger.canonic(entry_spec.origin), "module": launch_target})
            initial_action = debugger.wait_for_initial_setup()
            if initial_action in {"step", "next"}:
                debugger.set_step()
            debugger.run_module(launch_target, launch_args)
        else:
            target_script = os.path.abspath(launch_target)
            sys.argv = [target_script, *launch_args]
            send_event("started", {"file": debugger.canonic(target_script)})
            initial_action = debugger.wait_for_initial_setup()
            if initial_action in {"step", "next"}:
                debugger.set_step()
            debugger.run_script(target_script)
    except SystemExit:
        pass
    except Exception:
        send_event(
            "fatal",
            {
                "message": "Unhandled debugger harness failure",
                "traceback": traceback.format_exc(),
            },
        )

    send_event("finished")


if __name__ == "__main__":
    main()
'''


class PythonDebuggerBackend(DebuggerBackend):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.runner_script_path: str | None = None
        self.user_script_path: str | None = None
        self._pending_breakpoints: dict[str, list[dict]] = {}
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._state = ExecutionState.IDLE
        self._stop_stage = 0

        self.process.readyReadStandardOutput.connect(self._handle_stdout_ready)
        self.process.readyReadStandardError.connect(self._handle_stderr_ready)
        self.process.finished.connect(self._handle_process_finished)

    @property
    def state(self) -> ExecutionState:
        return self._state

    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints: dict[str, list[dict]]) -> None:
        self.stop_debugging(clean_only=True)

        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._pending_breakpoints = normalize_breakpoint_map(breakpoints)
        self._stop_stage = 0

        runner_tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py", encoding="utf-8")
        runner_tmp.write(DEBUGGER_HARNESS_CODE)
        runner_tmp.close()
        self.runner_script_path = runner_tmp.name

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

        self._apply_state(ExecutionState.STARTING)
        self.process.setWorkingDirectory(str(launch_request.working_directory or ""))

        process_environment = QProcessEnvironment.systemEnvironment()
        for key, value in launch_request.environment.items():
            process_environment.insert(str(key), str(value))
        roots = [
            str(path).strip()
            for path in (launch_request.working_directory, os.path.dirname(target_script_path), os.getcwd())
            if str(path or "").strip()
        ]
        process_environment.insert("PYTPO_DEBUG_PROJECT_ROOTS", json.dumps(roots))
        self.process.setProcessEnvironment(process_environment)

        launch_target = str(launch_request.module_name if launch_request.launch_kind == DebugLaunchKind.MODULE else target_script_path)
        self.process.start(
            interpreter,
            [self.runner_script_path, launch_request.launch_kind.value, launch_target, *launch_request.arguments],
        )

    def stop_debugging(self, clean_only: bool = False) -> None:
        if self.process.state() != QProcess.NotRunning:
            self._apply_state(ExecutionState.STOPPING)
            self.send_command("quit")
            if not self.process.waitForFinished(300):
                self.process.kill()
                self.process.waitForFinished()

        if clean_only:
            self._cleanup_temp_files()
            self._apply_state(ExecutionState.IDLE)
            self._stop_stage = 0

    def request_stop(self) -> int:
        if self.process.state() == QProcess.NotRunning:
            return 0

        current_state = self._state
        self._apply_state(ExecutionState.STOPPING)

        if self._stop_stage <= 0:
            self._stop_stage = 1
            if current_state == ExecutionState.PAUSED:
                if not self.send_command("quit"):
                    self.process.terminate()
            else:
                self.process.terminate()
            return 1

        if self._stop_stage == 1:
            self._stop_stage = 2
            self.process.terminate()
            return 2

        self._stop_stage = 3
        self.process.kill()
        return 3

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
        self.finished.emit()

    def _cleanup_temp_files(self) -> None:
        for path in (self.runner_script_path, self.user_script_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self.runner_script_path = None
        self.user_script_path = None
