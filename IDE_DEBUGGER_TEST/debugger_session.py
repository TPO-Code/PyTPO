import json
import os
import sys
import tempfile

from PySide6.QtCore import QProcess, QProcessEnvironment

from debugger_backend import DebugLaunchRequest, DebuggerBackend, ExecutionState


DEBUGGER_HARNESS_CODE = r'''
import bdb
import json
import os
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

    def user_line(self, frame):
        filename = self.canonic(frame.f_code.co_filename)
        if filename != self.mainpyfile:
            return

        self.paused_frame = frame
        send_event("stop", {
            "file": filename,
            "line": frame.f_lineno,
            "function": frame.f_code.co_name,
            "locals": self.get_safe_dict(frame.f_locals),
            "globals": self.get_safe_globals(frame.f_globals),
        })
        self.wait_for_command()

    def user_exception(self, frame, exc_info):
        exc_type, exc_value, exc_tb = exc_info
        filename = self.canonic(frame.f_code.co_filename)
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        send_event("exception", {
            "file": filename,
            "line": frame.f_lineno,
            "type": getattr(exc_type, "__name__", str(exc_type)),
            "message": str(exc_value),
            "traceback": tb_text,
        })

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
                self.set_step()
                return

            if action == "next":
                if self.paused_frame is not None:
                    self.set_next(self.paused_frame)
                else:
                    self.set_step()
                return

            if action == "continue":
                self.set_continue()
                return

            if action == "quit":
                raise SystemExit(0)

            if action == "set_breakpoints":
                lines = cmd.get("lines", [])
                self.clear_all_file_breaks(self.mainpyfile)
                for line_no in lines:
                    try:
                        self.set_break(self.mainpyfile, int(line_no))
                    except Exception:
                        pass
                send_event("breakpoints_set", {"lines": lines})

    def run_script(self, filename):
        self.mainpyfile = self.canonic(filename)

        script_dir = os.path.dirname(os.path.abspath(filename))
        if script_dir and script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        with open(filename, "rb") as f:
            source = f.read()

        code = compile(source, filename, "exec")
        globals_dict = {
            "__name__": "__main__",
            "__file__": filename,
            "__package__": None,
            "__cached__": None,
        }

        self.runctx(code, globals_dict, globals_dict)


def main():
    if len(sys.argv) < 2:
        send_event("fatal", {"message": "Missing target script path"})
        raise SystemExit(1)

    target_script = os.path.abspath(sys.argv[1])
    sys.argv = [target_script, *sys.argv[2:]]
    debugger = RemoteDebugger()

    send_event("started", {"file": debugger.canonic(target_script)})

    try:
        debugger.run_script(target_script)
    except SystemExit:
        pass
    except Exception:
        send_event("fatal", {
            "message": "Unhandled debugger harness failure",
            "traceback": traceback.format_exc(),
        })

    send_event("finished")


if __name__ == "__main__":
    main()
'''


class PythonDebuggerBackend(DebuggerBackend):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.runner_script_path = None
        self.user_script_path = None
        self._pending_breakpoints = []
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._state = ExecutionState.IDLE

        self.process.readyReadStandardOutput.connect(self._handle_stdout_ready)
        self.process.readyReadStandardError.connect(self._handle_stderr_ready)
        self.process.finished.connect(self._handle_process_finished)

    @property
    def state(self):
        return self._state

    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints):
        self.stop_debugging(clean_only=True)

        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._pending_breakpoints = sorted(breakpoints)

        runner_tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py", encoding="utf-8")
        runner_tmp.write(DEBUGGER_HARNESS_CODE)
        runner_tmp.close()
        self.runner_script_path = runner_tmp.name

        target_script_path = launch_request.file_path
        if launch_request.use_source_snapshot or not target_script_path:
            user_tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py", encoding="utf-8")
            user_tmp.write(launch_request.source_text)
            user_tmp.close()
            self.user_script_path = user_tmp.name
            target_script_path = self.user_script_path
        else:
            self.user_script_path = None

        self._apply_state(ExecutionState.STARTING)
        if launch_request.working_directory:
            self.process.setWorkingDirectory(launch_request.working_directory)
        else:
            self.process.setWorkingDirectory("")

        process_environment = QProcessEnvironment.systemEnvironment()
        for key, value in launch_request.environment.items():
            process_environment.insert(str(key), str(value))
        self.process.setProcessEnvironment(process_environment)

        self.process.start(
            sys.executable,
            [self.runner_script_path, target_script_path, *launch_request.arguments],
        )

    def stop_debugging(self, clean_only=False):
        if self.process.state() != QProcess.NotRunning:
            self._apply_state(ExecutionState.STOPPING)
            self.send_command("quit")
            if not self.process.waitForFinished(300):
                self.process.kill()
                self.process.waitForFinished()

        if clean_only:
            self._cleanup_temp_files()
            self._apply_state(ExecutionState.IDLE)

    def set_breakpoints(self, lines):
        self.send_command("set_breakpoints", {"lines": sorted(lines)})

    def send_command(self, action, extra=None):
        if self.process.state() != QProcess.Running:
            return False

        payload = {"action": action}
        if extra:
            payload.update(extra)

        data = (json.dumps(payload) + "\n").encode("utf-8")
        self.process.write(data)

        if action in {"continue", "step", "next"}:
            self._apply_state(ExecutionState.RUNNING)

        return True

    def _apply_state(self, state):
        if self._state == state:
            return
        self._state = state
        self.stateChanged.emit(state.value)

    def _handle_stdout_ready(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._stdout_buffer += data
        self._consume_stdout_lines()

    def _handle_stderr_ready(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_buffer += data
        self._consume_stderr_lines()

    def _consume_stdout_lines(self):
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self.stdoutReceived.emit(line)

    def _consume_stderr_lines(self):
        prefix = "__DBG__:"
        while "\n" in self._stderr_buffer:
            line, self._stderr_buffer = self._stderr_buffer.split("\n", 1)
            if line.startswith(prefix):
                payload = line[len(prefix):]
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    self.protocolError.emit(line)
                    continue
                self._process_debug_event(msg)
            else:
                self.stderrReceived.emit(line)

    def _process_debug_event(self, msg):
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

        if event == "exception":
            self.exceptionRaised.emit(data)
            return

        if event == "fatal":
            self.fatalError.emit(data)
            return

        if event == "finished":
            return

    def _handle_process_finished(self):
        if self._stdout_buffer:
            self.stdoutReceived.emit(self._stdout_buffer)
            self._stdout_buffer = ""

        if self._stderr_buffer:
            for line in self._stderr_buffer.splitlines():
                if line:
                    self.stderrReceived.emit(line)
            self._stderr_buffer = ""

        self._cleanup_temp_files()
        self._apply_state(ExecutionState.IDLE)
        self.finished.emit()

    def _cleanup_temp_files(self):
        for path in (self.runner_script_path, self.user_script_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self.runner_script_path = None
        self.user_script_path = None
