from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from pytpo.ui import lint_manager


class LintManagerBackendFallbackTests(unittest.TestCase):
    def test_build_backend_command_uses_ide_runtime_interpreter(self) -> None:
        with patch("pytpo.ui.lint_manager.os.sys.executable", "/ide/python"):
            cmd = lint_manager._build_backend_command(
                backend="ruff",
                interpreter=lint_manager._lint_backend_interpreter(),
                target="/tmp/example.py",
                args_cfg={},
            )

        self.assertEqual(
            ["/ide/python", "-m", "ruff", "check", "--output-format", "json", "/tmp/example.py"],
            cmd,
        )

    def test_run_external_backend_uses_ide_runtime_only(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if cmd[0] == "/ide/python":
                payload = [
                    {
                        "filename": "/tmp/example.py",
                        "location": {"row": 3, "column": 1},
                        "end_location": {"row": 3, "column": 4},
                        "code": "F401",
                        "message": "`sys` imported but unused",
                    }
                ]
                return _Proc(returncode=1, stdout=json.dumps(payload), stderr="")
            raise FileNotFoundError(cmd[0])

        with patch("pytpo.ui.lint_manager.os.sys.executable", "/ide/python"):
            with patch("pytpo.ui.lint_manager.subprocess.run", side_effect=fake_run):
                result = lint_manager._run_external_backend(
                    backend="ruff",
                    file_path="/tmp/example.py",
                    source_text=None,
                    args_cfg={},
                    severity_overrides={},
                    project_root="/tmp/project",
                )

        self.assertEqual("ok", result["state"])
        self.assertEqual("F401", result["diagnostics"][0]["code"])
        self.assertEqual([["/ide/python", "-m", "ruff", "check", "--output-format", "json", "/tmp/example.py"]], calls)

    def test_run_external_backend_reports_missing_from_ide_runtime(self) -> None:
        def fake_run(_cmd, **_kwargs):
            return _Proc(returncode=1, stdout="", stderr="No module named ruff")

        with patch("pytpo.ui.lint_manager.os.sys.executable", "/ide/python"):
            with patch("pytpo.ui.lint_manager.subprocess.run", side_effect=fake_run):
                result = lint_manager._run_external_backend(
                    backend="ruff",
                    file_path="/tmp/example.py",
                    source_text=None,
                    args_cfg={},
                    severity_overrides={},
                    project_root="/tmp/project",
                )

        self.assertEqual("missing", result["state"])


class _Proc:
    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


if __name__ == "__main__":
    unittest.main()
