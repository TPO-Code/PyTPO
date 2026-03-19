from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pytpo.ui.controllers.execution_controller import ExecutionController


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, text: str, _timeout: int = 0) -> None:
        self.messages.append(str(text))


class _FakeIde:
    def __init__(self) -> None:
        self._status_bar = _StatusBar()

    def statusBar(self) -> _StatusBar:
        return self._status_bar


class _FakeEditor:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path


class _FakeExecutionController:
    def __init__(self, file_path: str, *, launch_ok: bool) -> None:
        self.ide = _FakeIde()
        self.project_root = str(Path(file_path).resolve().parent)
        self.console_run_manager = object()
        self._editor = _FakeEditor(file_path)
        self._launch_ok = bool(launch_ok)
        self.python_active = "Named Python Config"
        self.rust_active = "Named Rust Config"
        self.launches: list[str] = []
        self.cleared_targets: list[tuple[str, str, bool]] = []

    def current_editor(self):
        return self._editor

    def _save_all_dirty_editors_for_run(self) -> bool:
        return True

    def _save_editor_for_run(self, editor: _FakeEditor) -> str:
        return editor.file_path

    def _is_cpp_runnable_file(self, _file_path: str) -> bool:
        return False

    def _is_rust_runnable_file(self, _file_path: str) -> bool:
        return False

    def _is_python_runnable_file(self, _file_path: str) -> bool:
        return True

    def resolve_interpreter(self, _file_path: str) -> str:
        return "/usr/bin/python3"

    def resolve_run_in(self, file_path: str) -> str:
        return str(Path(file_path).resolve().parent)

    def _run_python_script_in_terminal(self, **_kwargs) -> bool:
        self.launches.append("python")
        return self._launch_ok

    def active_python_run_config_name(self, *, fallback_to_first: bool = False) -> str:
        _ = fallback_to_first
        return self.python_active

    def active_rust_run_config_name(self, *, fallback_to_first: bool = False) -> str:
        _ = fallback_to_first
        return self.rust_active

    def set_active_python_run_config(self, config_name: str, *, announce: bool = True) -> bool:
        self.python_active = str(config_name or "").strip()
        self.cleared_targets.append(("python", self.python_active, bool(announce)))
        return True

    def set_active_rust_run_config(self, config_name: str, *, announce: bool = True) -> bool:
        self.rust_active = str(config_name or "").strip()
        self.cleared_targets.append(("rust", self.rust_active, bool(announce)))
        return True


class ExecutionControllerTests(unittest.TestCase):
    def test_run_current_file_clears_named_run_targets_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "main.py"
            path.write_text("print('hello')\n", encoding="utf-8")
            controller = _FakeExecutionController(str(path), launch_ok=True)
            controller._remember_run_current_file_target = (
                lambda: ExecutionController._remember_run_current_file_target(controller)
            )

            ExecutionController.run_current_file(controller)

            self.assertEqual(controller.launches, ["python"])
            self.assertEqual(controller.python_active, "")
            self.assertEqual(controller.rust_active, "")
            self.assertEqual(
                controller.cleared_targets,
                [("python", "", False), ("rust", "", False)],
            )

    def test_run_current_file_keeps_named_run_targets_when_launch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "main.py"
            path.write_text("print('hello')\n", encoding="utf-8")
            controller = _FakeExecutionController(str(path), launch_ok=False)
            controller._remember_run_current_file_target = (
                lambda: ExecutionController._remember_run_current_file_target(controller)
            )

            ExecutionController.run_current_file(controller)

            self.assertEqual(controller.launches, ["python"])
            self.assertEqual(controller.python_active, "Named Python Config")
            self.assertEqual(controller.rust_active, "Named Rust Config")
            self.assertEqual(controller.cleared_targets, [])


if __name__ == "__main__":
    unittest.main()
