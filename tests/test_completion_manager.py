from __future__ import annotations

import json
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication

from barley_ide.ui.completion_manager import CompletionManager, _CompletionPayload, _fallback_attribute_candidates


def _qt_app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


class CompletionFallbackTests(unittest.TestCase):
    def test_imported_module_attribute_fallback_lists_module_members(self) -> None:
        source = (
            "import argparse\n"
            "import sys\n"
            "\n"
            "def main():\n"
            "    parser = argparse.Ar\n"
        )

        items = _fallback_attribute_candidates(
            source,
            5,
            len("    parser = argparse.Ar"),
            "Ar",
            100,
        )

        labels = {str(item.get("label") or "") for item in items}
        self.assertIn("ArgumentParser", labels)


class CompletionManagerJediContextTests(unittest.TestCase):
    def setUp(self) -> None:
        _qt_app()
        self.manager = CompletionManager(
            project_root="/tmp/project",
            canonicalize=lambda p: str(p or ""),
            resolve_interpreter=lambda _p: "/project/.venv/bin/python",
            is_path_excluded=lambda _path, _feature: False,
        )
        self.addCleanup(self.manager.shutdown)

    def test_completion_uses_ide_worker_and_passes_project_analysis_context(self) -> None:
        captured: dict[str, object] = {}

        class _FakeServer:
            def request(self, payload: dict, timeout_s: float = 1.2) -> dict:
                captured["payload"] = dict(payload)
                captured["timeout_s"] = timeout_s
                return {"state": "ok", "items": []}

            def shutdown(self) -> None:
                return None

        self.manager._worker_interpreter = "/ide/python"
        self.manager._get_server = lambda worker_interpreter, project_root: captured.setdefault(  # type: ignore[method-assign]
            "server",
            (worker_interpreter, project_root),
        ) and _FakeServer()

        payload = _CompletionPayload(
            file_path="/tmp/project/example.py",
            source_text="import argparse\nargparse.Ar\n",
            line=2,
            column=len("argparse.Ar"),
            prefix="Ar",
            token=1,
            reason="manual",
            completion_cfg=dict(self.manager._completion_cfg),
            interpreter="/project/.venv/bin/python",
            analysis_sys_path=["/tmp/project", "/project/.venv/lib/python3.11/site-packages"],
            project_root="/tmp/project",
            recency={},
        )

        self.manager._run_completion_payload_fast(payload)

        self.assertEqual(captured["server"], ("/ide/python", "/tmp/project"))
        req = captured["payload"]
        self.assertEqual(req["analysis_interpreter"], "/project/.venv/bin/python")
        self.assertEqual(
            req["analysis_sys_path"],
            ["/tmp/project", "/project/.venv/lib/python3.11/site-packages"],
        )

    def test_fallback_completion_uses_project_analysis_providers(self) -> None:
        class _FakeServer:
            def request(self, payload: dict, timeout_s: float = 1.2) -> dict:
                _ = payload, timeout_s
                return {"state": "failed", "items": []}

            def shutdown(self) -> None:
                return None

        self.manager._get_server = lambda worker_interpreter, project_root: _FakeServer()  # type: ignore[method-assign]
        self.manager._analysis_module_members = lambda **kwargs: [  # type: ignore[method-assign]
            {
                "label": "ArgumentParser",
                "insert_text": "ArgumentParser",
                "kind": "class",
                "detail": "",
                "source": "fallback",
                "source_scope": "project",
            }
        ]
        self.manager._analysis_module_candidates = lambda **kwargs: []  # type: ignore[method-assign]
        self.manager._analysis_builtins = lambda **kwargs: []  # type: ignore[method-assign]

        payload = _CompletionPayload(
            file_path="/tmp/project/example.py",
            source_text="import argparse\nargparse.Ar\n",
            line=2,
            column=len("argparse.Ar"),
            prefix="Ar",
            token=1,
            reason="manual",
            completion_cfg=dict(self.manager._completion_cfg),
            interpreter="/project/.venv/bin/python",
            analysis_sys_path=["/tmp/project", "/project/.venv/lib/python3.11/site-packages"],
            project_root="/tmp/project",
            recency={},
        )

        result = self.manager._run_completion_payload_fast(payload)

        self.assertEqual(result["backend"], "fallback")
        labels = {str(item.get("label") or "") for item in result["items"]}
        self.assertIn("ArgumentParser", labels)

    def test_analysis_sys_path_is_cached_per_interpreter(self) -> None:
        completed = mock.Mock()
        completed.returncode = 0
        completed.stdout = json.dumps(["/venv/lib/python3.11", "/venv/lib/python3.11/site-packages"])

        with mock.patch("barley_ide.ui.completion_manager.subprocess.run", return_value=completed) as run_mock:
            first = self.manager._resolve_analysis_sys_path("/project/.venv/bin/python")
            second = self.manager._resolve_analysis_sys_path("/project/.venv/bin/python")

        self.assertEqual(first, second)
        self.assertEqual(run_mock.call_count, 1)

    def test_imported_module_alias_attribute_fallback_lists_module_members(self) -> None:
        source = (
            "import argparse as ap\n"
            "\n"
            "def main():\n"
            "    parser = ap.Ar\n"
        )

        items = _fallback_attribute_candidates(
            source,
            4,
            len("    parser = ap.Ar"),
            "Ar",
            100,
        )

        labels = {str(item.get("label") or "") for item in items}
        self.assertIn("ArgumentParser", labels)


if __name__ == "__main__":
    unittest.main()
