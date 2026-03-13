from __future__ import annotations

import json
import os
import selectors
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QProcess, QTimer

from pytpo.ui.debugger.breakpoint_store import DebuggerBreakpointStore
from pytpo.ui.debugger.backend import DebugLaunchKind, DebugLaunchRequest, ExecutionState
from pytpo.ui.debugger.debugpy_backend import DebugpyPythonDebuggerBackend
from pytpo.ui.debugger.lldb_dap_backend import LldbDapDebuggerBackend
from pytpo.ui.debugger.python_backend import BdbPythonDebuggerBackend, DEBUGGER_HARNESS_CODE, normalize_breakpoint_map
from pytpo.ui.debugger.python_backend import PythonDebuggerBackend
from pytpo.ui.debugger.session_widget import DebuggerSessionWidget
from pytpo.ui.debugger.terminal_bridge import DebugTerminalBridge
from pytpo.ui.debugger_support import debugger_breakpoints_supported_for_path
from pytpo.ui.console_run_manager import ConsoleRunManager
from pytpo.ui.controllers.execution_controller import ExecutionController


class _FakeSettingsManager:
    def __init__(self) -> None:
        self._scoped: dict[str, dict] = {"project": {}, "ide": {}}

    def get(self, key: str, *, scope_preference: str, default=None):
        node = self._scoped.get(scope_preference, {})
        for part in str(key or "").split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node.get(part)
        return node

    def set(self, key: str, value, scope: str) -> None:
        node = self._scoped.setdefault(scope, {})
        parts = str(key or "").split(".")
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value

    def save_all(self, **_kwargs) -> None:
        return None


class _FakeIde:
    def __init__(self) -> None:
        self.settings_manager = _FakeSettingsManager()
        self._status_messages: list[tuple[str, int]] = []
        self.console_run_manager = None
        self.execution_controller = None

    @staticmethod
    def _canonical_path(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def statusBar(self):
        return self

    def showMessage(self, message: str, timeout: int = 0) -> None:
        self._status_messages.append((str(message), int(timeout)))

    def _refresh_runtime_settings_from_manager(self) -> None:
        return None


class _FakeDebugIoHost:
    def __init__(self) -> None:
        self.launches: list[dict] = []
        self.inputs: list[str] = []
        self.started = False

    def start_debug_io_terminal(
        self,
        *,
        label: str,
        cwd: str,
        argv: list[str],
        env: dict[str, str | None],
        start_stopped: bool = False,
    ) -> int:
        self.launches.append(
            {
                "label": str(label),
                "cwd": str(cwd),
                "argv": list(argv),
                "env": dict(env),
                "start_stopped": bool(start_stopped),
            }
        )
        self.started = True
        return 4321

    def send_debug_io_input(self, text: str) -> bool:
        if not self.started:
            return False
        self.inputs.append(str(text))
        return True

    def debug_io_terminal_available(self) -> bool:
        return self.started


class _FakeSessionHost(QObject):
    def start_debug_io_terminal(self, *, label: str, cwd: str, argv: list[str], env: dict[str, str | None], start_stopped: bool = False) -> int:
        return 1

    def send_debug_io_input(self, text: str) -> bool:
        return True

    def debug_io_terminal_available(self) -> bool:
        return True


class _CapturingConsoleRunManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_custom_command(self, *, file_key: str, label: str, run_in: str, command_block: str) -> None:
        self.calls.append(
            {
                "file_key": str(file_key),
                "label": str(label),
                "run_in": str(run_in),
                "command_block": str(command_block),
            }
        )


def _qt_app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def _can_open_loopback_socket() -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        sock.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _wait_for_signal(signal, *, predicate=None, timeout_ms: int = 5000):
    _qt_app()
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    payload: dict[str, tuple] = {}

    def on_signal(*args):
        if predicate is not None and not predicate(*args):
            return
        payload["args"] = args
        if loop.isRunning():
            loop.quit()

    def on_timeout():
        if loop.isRunning():
            loop.quit()

    signal.connect(on_signal)
    timer.timeout.connect(on_timeout)
    timer.start(timeout_ms)
    loop.exec()
    try:
        signal.disconnect(on_signal)
    except Exception:
        pass
    try:
        timer.timeout.disconnect(on_timeout)
    except Exception:
        pass
    if "args" not in payload:
        raise AssertionError("Timed out waiting for signal.")
    return payload["args"]


class DebuggerBreakpointStoreTests(unittest.TestCase):
    def test_debugger_support_accepts_rust_paths(self) -> None:
        self.assertTrue(debugger_breakpoints_supported_for_path("/tmp/example.rs"))
        self.assertFalse(debugger_breakpoints_supported_for_path("/tmp/example.txt"))

    def test_breakpoint_store_reads_legacy_and_rich_specs(self) -> None:
        ide = _FakeIde()
        target = ide._canonical_path("example.py")
        ide.settings_manager.set(
            "debugger",
            {
                "breakpoints": {
                    target: [
                        3,
                        {"line": 7, "condition": "x > 1", "hit_count": 2, "log_message": ""},
                    ]
                },
                "watches": ["x", "x", " y "],
            },
            "project",
        )

        store = DebuggerBreakpointStore(ide)

        self.assertEqual(store.breakpoints_for_path(target), {3, 7})
        self.assertEqual(
            store.breakpoint_specs_for_path(target),
            [
                {"line": 3, "condition": "", "hit_count": 0, "log_message": ""},
                {"line": 7, "condition": "x > 1", "hit_count": 2, "log_message": ""},
            ],
        )
        self.assertEqual(store.watch_expressions(), ["x", "y"])

    def test_breakpoint_store_persists_rich_specs_and_watches(self) -> None:
        ide = _FakeIde()
        target = ide._canonical_path("sample.py")
        store = DebuggerBreakpointStore(ide)

        store.set_breakpoint_specs_for_path(
            target,
            [
                {"line": 11, "condition": "flag", "hit_count": 0, "log_message": ""},
                {"line": 15, "condition": "", "hit_count": 3, "log_message": ""},
            ],
        )
        store.set_watch_expressions(["value", "value", " result "])

        self.assertEqual(
            ide.settings_manager.get("debugger.breakpoints", scope_preference="project", default={}),
            {
                target: [
                    {"line": 11, "condition": "flag", "hit_count": 0, "log_message": ""},
                    {"line": 15, "condition": "", "hit_count": 3, "log_message": ""},
                ]
            },
        )
        self.assertEqual(
            ide.settings_manager.get("debugger.watches", scope_preference="project", default=[]),
            ["value", "result"],
        )


class PythonDebuggerHarnessTests(unittest.TestCase):
    def test_normalize_breakpoint_map_filters_invalid_entries(self) -> None:
        normalized = normalize_breakpoint_map(
            {
                "/tmp/a.py": [
                    {"line": 5, "condition": "ok", "hit_count": 0, "log_message": ""},
                    {"line": 5, "condition": "dup", "hit_count": 1, "log_message": "skip"},
                    {"line": 0, "condition": "", "hit_count": 0, "log_message": ""},
                ],
                "": [{"line": 1}],
            }
        )
        self.assertEqual(
            normalized,
            {
                "/tmp/a.py": [
                    {"line": 5, "condition": "ok", "hit_count": 0, "log_message": ""},
                ]
            },
        )

    def test_harness_hits_imported_module_breakpoint_and_evaluates_watch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_path = root / "main.py"
            helper_path = root / "helper.py"
            harness_path = root / "debugger_harness.py"

            main_path.write_text("from helper import work\nwork()\n", encoding="utf-8")
            helper_source = textwrap.dedent(
                """
                def work():
                    value = 41
                    value += 1
                    return value
                """
            ).lstrip()
            helper_path.write_text(helper_source, encoding="utf-8")
            harness_path.write_text(DEBUGGER_HARNESS_CODE, encoding="utf-8")

            proc = subprocess.Popen(
                [sys.executable, str(harness_path), "script", str(main_path)],
                cwd=str(root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTPO_DEBUG_PROJECT_ROOTS": json.dumps([str(root)])},
            )
            try:
                started = self._read_event(proc, expected="started")
                self.assertEqual(Path(started["data"]["file"]).name, "main.py")

                self._send_command(
                    proc,
                    {
                        "action": "set_breakpoints",
                        "breakpoints": {
                            os.path.normcase(os.path.abspath(str(helper_path))): [
                                {"line": 3, "condition": "", "hit_count": 0, "log_message": ""}
                            ]
                        },
                    },
                )
                self._send_command(proc, {"action": "set_watches", "expressions": ["value"]})
                self._send_command(proc, {"action": "continue"})

                event = self._read_event(proc, expected="stop")
                self.assertEqual(Path(event["data"]["file"]).name, "helper.py")
                self.assertEqual(int(event["data"]["line"]), 3)
                self.assertEqual(event["data"].get("watches"), [{"expression": "value", "status": "ok", "value": "41"}])

                self._send_command(proc, {"action": "evaluate", "expression": "value * 2"})
                evaluation = self._read_event(proc, expected="evaluation_result")
                self.assertEqual(evaluation["data"], {"expression": "value * 2", "status": "ok", "value": "82"})

                self._send_command(proc, {"action": "continue"})
                self._read_event(proc, expected="finished")
                self.assertEqual(proc.wait(timeout=5), 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                if proc.stdin is not None:
                    proc.stdin.close()
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()

    def _send_command(self, proc: subprocess.Popen[str], payload: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def _read_event(self, proc: subprocess.Popen[str], *, expected: str, timeout: float = 5.0) -> dict:
        assert proc.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stderr, selectors.EVENT_READ)
        try:
            end_time = time.monotonic() + timeout
            while time.monotonic() < end_time:
                ready = selector.select(timeout=0.1)
                if not ready:
                    continue
                line = proc.stderr.readline()
                if not line:
                    continue
                if not line.startswith("__DBG__:"):
                    continue
                event = json.loads(line[len("__DBG__:") :])
                if event.get("event") == expected:
                    return event
        finally:
            selector.close()
        raise AssertionError(f"Timed out waiting for debugger event: {expected}")


class PythonDebuggerBackendUnitTests(unittest.TestCase):
    def test_stop_plan_escalates_from_polite_to_kill(self) -> None:
        self.assertEqual(BdbPythonDebuggerBackend._stop_plan(0, ExecutionState.PAUSED), (1, "quit"))
        self.assertEqual(BdbPythonDebuggerBackend._stop_plan(0, ExecutionState.RUNNING), (1, "terminate"))
        self.assertEqual(BdbPythonDebuggerBackend._stop_plan(1, ExecutionState.RUNNING), (2, "terminate"))
        self.assertEqual(BdbPythonDebuggerBackend._stop_plan(2, ExecutionState.RUNNING), (3, "kill"))

    def test_resolved_working_directory_falls_back_to_script_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "sample.py")
            Path(script_path).write_text("print('ok')\n", encoding="utf-8")
            request = DebugLaunchRequest(
                file_path=script_path,
                source_text="",
                launch_kind=DebugLaunchKind.SCRIPT,
                working_directory=os.path.join(tmpdir, "missing"),
            )
            self.assertEqual(
                BdbPythonDebuggerBackend._resolved_working_directory(request, script_path),
                os.path.normcase(os.path.abspath(tmpdir)),
            )

    def test_build_rust_debug_build_command_uses_json_output(self) -> None:
        controller = ExecutionController.__new__(ExecutionController)
        command = ExecutionController._build_rust_debug_build_command(
            controller,
            package="demo_pkg",
            binary="demo_bin",
            profile="release",
            features="serde",
            command_type="test",
        )
        self.assertEqual(
            command,
            (
                "cargo",
                "test",
                "--message-format=json-render-diagnostics",
                "--package",
                "demo_pkg",
                "--bin",
                "demo_bin",
                "--features",
                "serde",
                "--release",
                "--no-run",
            ),
        )

    def test_wrapper_passes_session_io_host_into_debugpy_backend(self) -> None:
        host = _FakeSessionHost()
        with mock.patch.object(PythonDebuggerBackend, "_debugpy_available", return_value=True):
            with mock.patch.object(PythonDebuggerBackend, "_debugpy_ready", return_value=True):
                backend = PythonDebuggerBackend(parent=host, ide=_FakeIde(), preferred_backend="debugpy")
        self.assertEqual(backend.backend_name(), "debugpy")
        bridge = getattr(backend._impl, "_terminal_bridge", None)
        self.assertIsNotNone(bridge)
        self.assertIs(getattr(bridge, "host", None), host)


class LldbDapBackendUnitTests(unittest.TestCase):
    def test_adapter_path_accepts_versioned_lldb_vscode_binary(self) -> None:
        with mock.patch.object(LldbDapDebuggerBackend, "_adapter_candidates", return_value=("lldb-dap", "lldb-vscode-14")):
            with mock.patch("pytpo.ui.debugger.lldb_dap_backend.shutil.which") as which_mock:
                which_mock.side_effect = lambda name: {"lldb-dap": "", "lldb-vscode-14": "/usr/bin/lldb-vscode-14"}.get(name, "")
                self.assertEqual(LldbDapDebuggerBackend.adapter_path(), "/usr/bin/lldb-vscode-14")

    def test_parse_cargo_json_stream_collects_rendered_messages_and_artifacts(self) -> None:
        stream = "\n".join(
            [
                json.dumps(
                    {
                        "reason": "compiler-message",
                        "message": {"rendered": "warning: sample warning\n --> src/main.rs:1:1"},
                    }
                ),
                json.dumps(
                    {
                        "reason": "compiler-artifact",
                        "executable": "/tmp/target/debug/demo",
                        "target": {"name": "demo", "kind": ["bin"]},
                    }
                ),
            ]
        )
        artifacts, rendered = LldbDapDebuggerBackend._parse_cargo_json_stream(stream)

        self.assertEqual(
            [(item.executable, item.target_name, item.target_kinds) for item in artifacts],
            [("/tmp/target/debug/demo", "demo", ("bin",))],
        )
        self.assertEqual(rendered, ["warning: sample warning", " --> src/main.rs:1:1"])

    def test_select_artifact_path_prefers_requested_name_and_kind(self) -> None:
        artifacts = [
            LldbDapDebuggerBackend._parse_cargo_json_stream(
                json.dumps(
                    {
                        "reason": "compiler-artifact",
                        "executable": "/tmp/target/debug/demo",
                        "target": {"name": "demo", "kind": ["bin"]},
                    }
                )
            )[0][0],
            LldbDapDebuggerBackend._parse_cargo_json_stream(
                json.dumps(
                    {
                        "reason": "compiler-artifact",
                        "executable": "/tmp/target/debug/deps/demo_tests",
                        "target": {"name": "demo", "kind": ["test"]},
                    }
                )
            )[0][0],
        ]

        self.assertEqual(
            LldbDapDebuggerBackend._select_artifact_path(
                artifacts,
                target_name="demo",
                target_kind="test",
            ),
            "/tmp/target/debug/deps/demo_tests",
        )

    def test_launch_roots_are_deduplicated_and_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            package_dir = Path(tmpdir) / "pkg"
            package_dir.mkdir()
            script_path = package_dir / "main.py"
            script_path.write_text("print('ok')\n", encoding="utf-8")
            request = DebugLaunchRequest(
                file_path=str(script_path),
                source_text="",
                launch_kind=DebugLaunchKind.SCRIPT,
                working_directory=tmpdir,
            )
            roots = BdbPythonDebuggerBackend._launch_roots_for_request(request, str(script_path), tmpdir)
            self.assertIn(os.path.normcase(os.path.abspath(tmpdir)), roots)
            self.assertIn(os.path.normcase(os.path.abspath(str(package_dir))), roots)
            self.assertEqual(len(roots), len(set(roots)))

    def test_process_error_message_covers_failed_start(self) -> None:
        self.assertEqual(
            BdbPythonDebuggerBackend._process_error_message(QProcess.ProcessError.FailedToStart),
            "Debugger process failed to start.",
        )

    def test_selector_defaults_to_bdb_backend(self) -> None:
        backend = PythonDebuggerBackend(ide=_FakeIde())
        self.assertEqual(backend.backend_name(), "debugpy" if DebugpyPythonDebuggerBackend.is_available() else "bdb")

    def test_selector_falls_back_from_unknown_backend(self) -> None:
        ide = _FakeIde()
        ide.settings_manager.set("debugger.python_backend", "unknown", "ide")
        backend = PythonDebuggerBackend(ide=ide)
        self.assertEqual(backend.backend_name(), "bdb")

    def test_selector_prefers_debugpy_when_available_and_ready(self) -> None:
        with mock.patch.object(PythonDebuggerBackend, "_debugpy_available", return_value=True):
            with mock.patch.object(PythonDebuggerBackend, "_debugpy_ready", return_value=True):
                backend = PythonDebuggerBackend(ide=_FakeIde())
        self.assertEqual(backend.backend_name(), "debugpy")

    def test_selector_uses_bdb_when_debugpy_is_available_but_not_ready(self) -> None:
        with mock.patch.object(PythonDebuggerBackend, "_debugpy_available", return_value=True):
            with mock.patch.object(PythonDebuggerBackend, "_debugpy_ready", return_value=False):
                backend = PythonDebuggerBackend(ide=_FakeIde())
        self.assertEqual(backend.backend_name(), "bdb")

    def test_selector_honors_explicit_debugpy_request_when_available(self) -> None:
        with mock.patch.object(PythonDebuggerBackend, "_debugpy_available", return_value=True):
            backend = PythonDebuggerBackend(ide=_FakeIde(), preferred_backend="debugpy")
        self.assertEqual(backend.backend_name(), "debugpy")

    def test_lldb_backend_uses_legacy_run_in_terminal_flag_for_lldb_vscode(self) -> None:
        host = _FakeDebugIoHost()
        backend = LldbDapDebuggerBackend()
        backend._terminal_bridge = DebugTerminalBridge(host)
        backend._adapter_path = "/usr/bin/lldb-vscode-14"
        backend._launch_request = DebugLaunchRequest(
            file_path="/tmp/main.rs",
            source_text="",
            launch_kind=DebugLaunchKind.EXECUTABLE,
            program_path="/tmp/target/debug/demo",
            working_directory="/tmp",
        )
        calls: list[tuple[str, dict]] = []
        backend._send_request = lambda command, arguments, callback: calls.append((command, dict(arguments))) or 1  # type: ignore[method-assign]
        backend._send_launch_request()
        self.assertTrue(calls)
        self.assertEqual(calls[0][0], "attach")
        self.assertEqual(calls[0][1].get("pid"), 4321)
        self.assertEqual(host.launches[0]["argv"][0], sys.executable)
        self.assertTrue(host.launches[0]["argv"][1].endswith("native_debug_launch_helper.py"))
        self.assertIn("--ptracer", host.launches[0]["argv"])
        self.assertEqual(host.launches[0]["argv"][-1], "/tmp/target/debug/demo")
        self.assertIs(host.launches[0]["start_stopped"], False)


class DebugpyBackendBridgeTests(unittest.TestCase):
    def test_debugpy_backend_reports_ready(self) -> None:
        self.assertTrue(DebugpyPythonDebuggerBackend.implementation_ready())

    def test_debugpy_backend_stop_plan_escalates_without_bridge(self) -> None:
        self.assertEqual(DebugpyPythonDebuggerBackend._stop_plan(0, ExecutionState.RUNNING), (1, "disconnect"))
        self.assertEqual(DebugpyPythonDebuggerBackend._stop_plan(1, ExecutionState.RUNNING), (2, "terminate"))
        self.assertEqual(DebugpyPythonDebuggerBackend._stop_plan(2, ExecutionState.RUNNING), (3, "kill"))

    def test_debugpy_breakpoint_signature_is_stable_for_identical_specs(self) -> None:
        first = DebugpyPythonDebuggerBackend._breakpoint_signature({"a.py": [{"line": 3, "condition": "", "hit_count": 0, "log_message": ""}]})
        second = DebugpyPythonDebuggerBackend._breakpoint_signature({"a.py": [{"line": 3, "condition": "", "hit_count": 0, "log_message": ""}]})
        third = DebugpyPythonDebuggerBackend._breakpoint_signature({"a.py": [{"line": 4, "condition": "", "hit_count": 0, "log_message": ""}]})
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_debugpy_exited_event_requests_disconnect(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        calls: list[bool] = []
        backend._send_disconnect_request = lambda *, terminate_debuggee: calls.append(bool(terminate_debuggee))  # type: ignore[method-assign]
        backend._handle_event("exited", {"exitCode": 0})
        self.assertEqual(calls, [False])
        self.assertEqual(backend._exited_info, {"exit_code": 0, "exit_status": "finished"})

    def test_debugpy_process_event_tracks_target_pid(self) -> None:
        _qt_app()
        backend = DebugpyPythonDebuggerBackend()
        backend._handle_event("process", {"systemProcessId": 4321})
        self.assertEqual(backend._target_process_id, 4321)
        self.assertTrue(backend._target_exit_poll.isActive())
        backend._target_exit_poll.stop()

    def test_debugpy_pid_poll_requests_disconnect_when_target_exits(self) -> None:
        _qt_app()
        backend = DebugpyPythonDebuggerBackend()
        backend._target_process_id = 4321
        backend.process = mock.Mock(state=mock.Mock(return_value=QProcess.Running))
        calls: list[bool] = []
        backend._send_disconnect_request = lambda *, terminate_debuggee: calls.append(bool(terminate_debuggee))  # type: ignore[method-assign]
        with mock.patch.object(DebugpyPythonDebuggerBackend, "_process_exists", return_value=False):
            backend._poll_target_process_exit()
        self.assertEqual(calls, [False])
        self.assertEqual(backend._exited_info, {"exit_code": 0, "exit_status": "finished"})

    def test_debugpy_run_in_terminal_request_launches_terminal_session(self) -> None:
        host = _FakeDebugIoHost()
        backend = DebugpyPythonDebuggerBackend()
        backend._terminal_bridge = DebugTerminalBridge(host)
        backend._launch_request = DebugLaunchRequest(
            file_path="/tmp/example.py",
            source_text="",
            launch_kind=DebugLaunchKind.SCRIPT,
            interpreter=sys.executable,
            working_directory="/tmp",
        )
        packets: list[dict] = []
        backend._send_response = (
            lambda request_seq, command, *, success, body, message="": packets.append(
                {
                    "request_seq": request_seq,
                    "command": command,
                    "success": success,
                    "body": dict(body),
                    "message": message,
                }
            )
        )  # type: ignore[method-assign]

        backend._handle_dap_message(
            {
                "type": "request",
                "seq": 7,
                "command": "runInTerminal",
                "arguments": {
                    "cwd": "/tmp",
                    "args": [sys.executable, "/tmp/example.py", "--flag"],
                    "env": {"DEMO": "1"},
                },
            }
        )

        self.assertEqual(len(host.launches), 1)
        call = host.launches[0]
        self.assertEqual(call["cwd"], "/tmp")
        self.assertEqual(call["argv"], [sys.executable, "/tmp/example.py", "--flag"])
        self.assertEqual(call["env"], {"DEMO": "1"})
        self.assertEqual(packets, [{"request_seq": 7, "command": "runInTerminal", "success": True, "body": {}, "message": ""}])
        backend._state = ExecutionState.RUNNING
        self.assertTrue(backend.supports_stdin())
        self.assertTrue(backend.send_stdin("hello"))
        self.assertEqual(host.inputs, ["hello"])


class DebugTerminalBridgeTests(unittest.TestCase):
    def test_bridge_forwards_launch_and_input_to_host(self) -> None:
        host = _FakeDebugIoHost()
        bridge = DebugTerminalBridge(host)
        self.assertFalse(bridge.available())
        self.assertTrue(
            bridge.launch(
                label="Debug I/O: demo",
                cwd="/tmp",
                argv=["python3", "/tmp/main.py"],
                env={"DEMO": "1"},
            )
        )
        self.assertTrue(bridge.available())
        self.assertEqual(
            host.launches,
            [{"label": "Debug I/O: demo", "cwd": "/tmp", "argv": ["python3", "/tmp/main.py"], "env": {"DEMO": "1"}, "start_stopped": False}],
        )
        self.assertTrue(bridge.send_input("hello"))
        self.assertEqual(host.inputs, ["hello"])

    def test_debugpy_force_shutdown_terminates_and_kills_when_needed(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        backend.process = mock.Mock(
            state=mock.Mock(return_value=QProcess.Running),
            waitForFinished=mock.Mock(return_value=False),
            terminate=mock.Mock(),
            kill=mock.Mock(),
        )
        backend._force_shutdown_adapter()
        backend.process.terminate.assert_called_once_with()
        backend.process.kill.assert_called_once_with()

    def test_debugpy_launch_request_respects_just_my_code_flag(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        backend._launch_request = DebugLaunchRequest(
            file_path="/tmp/example.py",
            source_text="",
            launch_kind=DebugLaunchKind.SCRIPT,
            interpreter=sys.executable,
            working_directory="/tmp",
            just_my_code=False,
        )
        calls: list[tuple[str, dict]] = []
        backend._send_request = lambda command, arguments, callback: calls.append((command, dict(arguments))) or 1  # type: ignore[method-assign]
        backend._send_launch_request()
        self.assertTrue(calls)
        self.assertEqual(calls[0][0], "launch")
        self.assertIs(calls[0][1].get("justMyCode"), False)

    def test_debugpy_launch_failure_emits_fatal_error(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        payloads: list[dict] = []
        backend.fatalError.connect(lambda data: payloads.append(dict(data)))
        backend._handle_launch_response(False, {}, "launch failed")
        self.assertEqual(payloads, [{"message": "launch failed", "traceback": ""}])

    def test_debugpy_configuration_failure_emits_fatal_error(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        payloads: list[dict] = []
        backend.fatalError.connect(lambda data: payloads.append(dict(data)))
        backend._handle_configuration_done(False, {}, "config failed")
        self.assertEqual(payloads, [{"message": "config failed", "traceback": ""}])

    def test_debugpy_stop_debugging_disconnects_without_terminate_after_exit(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        backend._target_process_id = 4321
        backend._exited_info = {"exit_code": 0, "exit_status": "finished"}
        backend.process = mock.Mock(
            state=mock.Mock(return_value=QProcess.Running),
            waitForFinished=mock.Mock(return_value=True),
        )
        calls: list[bool] = []
        backend._send_disconnect_request = lambda *, terminate_debuggee: calls.append(bool(terminate_debuggee))  # type: ignore[method-assign]
        backend.stop_debugging()
        self.assertEqual(calls, [False])

    def test_debugpy_request_stop_disconnects_with_terminate_when_target_is_alive(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        backend._target_process_id = 4321
        backend.process = mock.Mock(state=mock.Mock(return_value=QProcess.Running))
        calls: list[bool] = []
        backend._send_disconnect_request = lambda *, terminate_debuggee: calls.append(bool(terminate_debuggee))  # type: ignore[method-assign]
        with mock.patch.object(DebugpyPythonDebuggerBackend, "_process_exists", return_value=True):
            stage = backend.request_stop()
        self.assertEqual(stage, 1)
        self.assertEqual(calls, [True])

    def test_debugpy_request_stop_disconnects_without_terminate_when_target_is_gone(self) -> None:
        backend = DebugpyPythonDebuggerBackend()
        backend._target_process_id = 4321
        backend.process = mock.Mock(state=mock.Mock(return_value=QProcess.Running))
        calls: list[bool] = []
        backend._send_disconnect_request = lambda *, terminate_debuggee: calls.append(bool(terminate_debuggee))  # type: ignore[method-assign]
        with mock.patch.object(DebugpyPythonDebuggerBackend, "_process_exists", return_value=False):
            stage = backend.request_stop()
        self.assertEqual(stage, 1)
        self.assertEqual(calls, [False])


@unittest.skipUnless(DebugpyPythonDebuggerBackend.is_available(), "debugpy not installed")
@unittest.skipUnless(_can_open_loopback_socket(), "loopback sockets unavailable")
class DebugpyBackendIntegrationTests(unittest.TestCase):
    def test_debugpy_backend_auto_finishes_simple_script(self) -> None:
        _qt_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "hello.py"
            script_path.write_text('print("Hello, world!")\n', encoding="utf-8")

            backend = DebugpyPythonDebuggerBackend()
            stdout_lines: list[str] = []
            ended_payloads: list[dict] = []
            backend.stdoutReceived.connect(stdout_lines.append)
            backend.processEnded.connect(lambda data: ended_payloads.append(dict(data)))
            try:
                backend.start_debugging(
                    DebugLaunchRequest(
                        file_path=str(script_path),
                        source_text="",
                        launch_kind=DebugLaunchKind.SCRIPT,
                        interpreter=sys.executable,
                        working_directory=tmpdir,
                    ),
                    {},
                )
                _wait_for_signal(backend.finished, timeout_ms=8000)
            finally:
                backend.stop_debugging(clean_only=True)

            self.assertIn("Hello, world!", stdout_lines)
            self.assertTrue(ended_payloads)
            self.assertEqual(int(ended_payloads[-1].get("exit_code") or -1), 0)
            self.assertEqual(str(ended_payloads[-1].get("exit_status") or ""), "finished")

    def test_debugpy_backend_hits_breakpoint_and_evaluates_watch(self) -> None:
        _qt_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "main.py"
            script_path.write_text("x = 40\nx += 2\nprint(x)\n", encoding="utf-8")

            backend = DebugpyPythonDebuggerBackend()
            try:
                backend.start_debugging(
                    DebugLaunchRequest(
                        file_path=str(script_path),
                        source_text="",
                        launch_kind=DebugLaunchKind.SCRIPT,
                        interpreter=sys.executable,
                        working_directory=tmpdir,
                    ),
                    {
                        os.path.normcase(os.path.abspath(str(script_path))): [
                            {"line": 2, "condition": "", "hit_count": 0, "log_message": ""}
                        ]
                    },
                )

                (paused_data,) = _wait_for_signal(
                    backend.paused,
                    predicate=lambda data: int(data.get("line") or 0) == 2,
                    timeout_ms=8000,
                )
                self.assertEqual(Path(str(paused_data.get("file") or "")).name, "main.py")

                backend.send_command("set_watches", {"expressions": ["x"]})
                (watch_data,) = _wait_for_signal(
                    backend.watchValuesUpdated,
                    predicate=lambda data: bool(data.get("watches")),
                    timeout_ms=4000,
                )
                self.assertEqual(
                    watch_data.get("watches"),
                    [{"expression": "x", "status": "ok", "value": "40"}],
                )

                backend.send_command("evaluate", {"expression": "x * 2"})
                (eval_data,) = _wait_for_signal(
                    backend.evaluationResult,
                    predicate=lambda data: str(data.get("expression") or "") == "x * 2",
                    timeout_ms=4000,
                )
                self.assertEqual(eval_data, {"expression": "x * 2", "status": "ok", "value": "80"})

                backend.send_command("next")
                (paused_again,) = _wait_for_signal(
                    backend.paused,
                    predicate=lambda data: int(data.get("line") or 0) == 3,
                    timeout_ms=4000,
                )
                self.assertEqual(
                    paused_again.get("watches"),
                    [{"expression": "x", "status": "ok", "value": "42"}],
                )

                backend.send_command("continue")
                _wait_for_signal(backend.finished, timeout_ms=8000)
            finally:
                backend.stop_debugging(clean_only=True)

    def test_debugpy_backend_stop_request_finishes_long_running_script(self) -> None:
        _qt_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "sleeper.py"
            script_path.write_text(
                "import time\n"
                "print('ready', flush=True)\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )

            backend = DebugpyPythonDebuggerBackend()
            stdout_lines: list[str] = []
            backend.stdoutReceived.connect(stdout_lines.append)
            try:
                backend.start_debugging(
                    DebugLaunchRequest(
                        file_path=str(script_path),
                        source_text="",
                        launch_kind=DebugLaunchKind.SCRIPT,
                        interpreter=sys.executable,
                        working_directory=tmpdir,
                    ),
                    {},
                )

                _wait_for_signal(
                    backend.stdoutReceived,
                    predicate=lambda line: str(line).strip() == "ready",
                    timeout_ms=8000,
                )
                stage = backend.request_stop()
                self.assertEqual(stage, 1)
                _wait_for_signal(backend.finished, timeout_ms=8000)
            finally:
                backend.stop_debugging(clean_only=True)

            self.assertIn("ready", stdout_lines)

    def test_debugpy_backend_hits_breakpoint_inside_qt_event_loop(self) -> None:
        _qt_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "event_loop.py"
            script_path.write_text(
                "from PySide6.QtCore import QCoreApplication, QTimer\n"
                "app = QCoreApplication([])\n"
                "state = {'value': 0}\n"
                "\n"
                "def tick():\n"
                "    state['value'] += 1\n"
                "    print(f\"tick {state['value']}\", flush=True)\n"
                "    app.quit()\n"
                "\n"
                "QTimer.singleShot(0, tick)\n"
                "app.exec()\n"
                "print('done', flush=True)\n",
                encoding="utf-8",
            )

            backend = DebugpyPythonDebuggerBackend()
            stdout_lines: list[str] = []
            backend.stdoutReceived.connect(stdout_lines.append)
            try:
                backend.start_debugging(
                    DebugLaunchRequest(
                        file_path=str(script_path),
                        source_text="",
                        launch_kind=DebugLaunchKind.SCRIPT,
                        interpreter=sys.executable,
                        working_directory=tmpdir,
                    ),
                    {
                        os.path.normcase(os.path.abspath(str(script_path))): [
                            {"line": 6, "condition": "", "hit_count": 0, "log_message": ""}
                        ]
                    },
                )

                (paused_data,) = _wait_for_signal(
                    backend.paused,
                    predicate=lambda data: int(data.get("line") or 0) == 6,
                    timeout_ms=8000,
                )
                self.assertEqual(Path(str(paused_data.get("file") or "")).name, "event_loop.py")

                backend.send_command("continue")
                _wait_for_signal(backend.finished, timeout_ms=8000)
            finally:
                backend.stop_debugging(clean_only=True)

            self.assertIn("tick 1", stdout_lines)
            self.assertIn("done", stdout_lines)

    def test_debugpy_backend_launches_package_module_from_src_layout(self) -> None:
        _qt_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "src"
            package_root = src_root / "demo_pkg"
            package_root.mkdir(parents=True)
            (package_root / "__init__.py").write_text("", encoding="utf-8")
            main_path = package_root / "__main__.py"
            main_path.write_text(
                "value = 1\n"
                "value += 1\n"
                "print(value)\n",
                encoding="utf-8",
            )

            backend = DebugpyPythonDebuggerBackend()
            try:
                backend.start_debugging(
                    DebugLaunchRequest(
                        file_path=str(main_path),
                        source_text="",
                        launch_kind=DebugLaunchKind.MODULE,
                        module_name="demo_pkg",
                        interpreter=sys.executable,
                        working_directory=tmpdir,
                    ),
                    {
                        os.path.normcase(os.path.abspath(str(main_path))): [
                            {"line": 2, "condition": "", "hit_count": 0, "log_message": ""}
                        ]
                    },
                )

                (paused_data,) = _wait_for_signal(
                    backend.paused,
                    predicate=lambda data: int(data.get("line") or 0) == 2,
                    timeout_ms=8000,
                )
                self.assertEqual(Path(str(paused_data.get("file") or "")).name, "__main__.py")
                backend.send_command("continue")
                _wait_for_signal(backend.finished, timeout_ms=8000)
            finally:
                backend.stop_debugging(clean_only=True)


class ExecutionControllerPythonModuleResolutionTests(unittest.TestCase):
    def test_python_debug_target_is_separate_from_run_target(self) -> None:
        ide = _FakeIde()
        ide.settings_manager.set(
            "build.python.run_configs",
            [
                {"name": "Run A", "script_path": "a.py"},
                {"name": "Run B", "script_path": "b.py"},
            ],
            "project",
        )
        ide._refresh_runtime_settings_from_manager = lambda: None  # type: ignore[attr-defined]
        controller = ExecutionController(ide)

        self.assertTrue(controller.set_active_python_run_config("Run A"))
        self.assertTrue(controller.set_active_python_debug_config("Run B"))
        self.assertEqual(controller.active_python_run_config_name(), "Run A")
        self.assertEqual(controller.active_python_debug_config_name(), "Run B")

    def test_resolve_python_module_entry_path_supports_src_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src_root = Path(tmpdir) / "src"
            package_root = src_root / "demo_pkg"
            package_root.mkdir(parents=True)
            (package_root / "__init__.py").write_text("", encoding="utf-8")
            main_path = package_root / "__main__.py"
            main_path.write_text("print('ok')\n", encoding="utf-8")

            controller = ExecutionController.__new__(ExecutionController)
            controller.project_root = tmpdir
            controller._canonical_path = staticmethod(lambda path: os.path.normcase(os.path.abspath(path)))  # type: ignore[attr-defined]

            resolved = ExecutionController._resolve_python_module_entry_path(controller, "demo_pkg", tmpdir)
            self.assertEqual(resolved, os.path.normcase(os.path.abspath(str(main_path))))

    def test_build_python_module_run_command_uses_dash_m(self) -> None:
        controller = ExecutionController.__new__(ExecutionController)
        command = ExecutionController._build_python_module_run_command(
            controller,
            run_in="/tmp/project",
            interpreter="/usr/bin/python3",
            module_name="demo_pkg",
            args_text="--flag value",
            env_assignments=[("PYTHONPATH", "/tmp/project/src")],
        )
        self.assertIn("cd /tmp/project", command)
        self.assertIn("export PYTHONPATH=/tmp/project/src", command)
        self.assertIn("/usr/bin/python3 -m demo_pkg --flag value", command)
        self.assertIn("__PYTPO_RUN_EXIT__", command)

    def test_console_run_manager_wraps_command_block_before_dispatch(self) -> None:
        wrapped = ConsoleRunManager._wrap_command_block_for_dispatch(
            "cargo run\nstatus=$?\nprintf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"\n"
        )
        self.assertTrue(wrapped.startswith("(\n"))
        self.assertTrue(wrapped.endswith("\n)"))
        self.assertIn("cargo run", wrapped)
        self.assertIn("__PYTPO_RUN_EXIT__", wrapped)
        self.assertNotIn("\n\n)", wrapped)

    def test_default_rust_run_uses_release_profile(self) -> None:
        ide = _FakeIde()
        ide.console_run_manager = _CapturingConsoleRunManager()
        ide.dock_terminal = mock.Mock()
        controller = ExecutionController(ide)
        controller.project_root = "/tmp/project"
        controller._canonical_path = staticmethod(lambda path: os.path.normcase(os.path.abspath(path)))  # type: ignore[attr-defined]
        controller._resolve_rust_workspace_root = lambda _path: "/tmp/project"  # type: ignore[method-assign]
        controller._run_config = lambda: {"focus_output_on_run": False}  # type: ignore[method-assign]

        ok = controller._run_default_rust_context(file_path="/tmp/project/src/main.rs", status_prefix="Running")

        self.assertTrue(ok)
        self.assertEqual(len(ide.console_run_manager.calls), 1)
        self.assertIn("cargo run --release", ide.console_run_manager.calls[0]["command_block"])


class DebuggerOutputFormattingTests(unittest.TestCase):
    def test_extract_output_reference_handles_python_traceback(self) -> None:
        ref = DebuggerSessionWidget._extract_output_reference('  File "/tmp/example.py", line 14, in run')
        self.assertEqual(ref, (8, 33, "/tmp/example.py", 14, 1))

    def test_extract_output_reference_handles_compiler_style_location(self) -> None:
        ref = DebuggerSessionWidget._extract_output_reference("/tmp/main.cpp:27:9: error: missing ';'")
        self.assertEqual(ref, (0, 18, "/tmp/main.cpp", 27, 9))

    def test_render_output_html_wraps_detected_reference_in_anchor(self) -> None:
        html_output = DebuggerSessionWidget._render_output_html(
            "[debug] paused at /tmp/example.py:21 in run",
            category="debug",
        )
        self.assertIn('class="dbg-line dbg-debug"', html_output)
        self.assertIn('<a href="pytpo-debug:?path=/tmp/example.py&amp;line=21&amp;col=1">/tmp/example.py:21</a>', html_output)

    def test_normalize_stream_output_rewrites_debugpy_just_my_code_skip_lines(self) -> None:
        line, category = DebuggerSessionWidget._normalize_stream_output(
            'Note: may have been skipped because of "justMyCode" option (default == true). Try setting "justMyCode": false in the debug configuration (e.g., launch.json).',
            category="stdout",
        )
        self.assertEqual(category, "debug")
        self.assertIn("Just My Code is enabled", line)


if __name__ == "__main__":
    unittest.main()
