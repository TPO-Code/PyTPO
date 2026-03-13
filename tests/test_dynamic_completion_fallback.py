import os
import subprocess
import sys
import unittest

from PySide6.QtCore import QCoreApplication

from pytpo.ui.completion_manager import (
    CompletionManager,
    _detect_context,
    _resolve_attribute_callable_target,
    _should_augment_dynamic_attribute_items,
)


SYSTEM_PYTHON = "/usr/bin/python3"


def _system_python_has_mutagen() -> bool:
    if not os.path.exists(SYSTEM_PYTHON):
        return False
    proc = subprocess.run(
        [SYSTEM_PYTHON, "-c", "import mutagen"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


@unittest.skipUnless(_system_python_has_mutagen(), "system mutagen is not available")
class DynamicCompletionFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def test_detect_context_marks_callable_attribute_target(self) -> None:
        source = "print(mutagen.File(file_path).)\n"
        context = _detect_context(source, 1, len("print(mutagen.File(file_path)."), "")

        self.assertEqual(context.get("mode"), "attribute")
        self.assertEqual(context.get("attribute_expr"), "mutagen.File(file_path)")
        self.assertEqual(context.get("callable_target"), "mutagen.File")
        self.assertTrue(
            _should_augment_dynamic_attribute_items(
                [
                    {"label": "__bool__", "insert_text": "__bool__"},
                    {"label": "__dir__", "insert_text": "__dir__"},
                ],
                context,
            )
        )

    def test_runtime_probe_recovers_mutagen_file_members(self) -> None:
        manager = CompletionManager(
            project_root=os.getcwd(),
            canonicalize=os.path.abspath,
            resolve_interpreter=lambda _path: SYSTEM_PYTHON,
            is_path_excluded=lambda _path, for_feature="completion": False,
        )
        self.addCleanup(manager.shutdown)

        items = manager._analysis_callable_return_members(
            interpreter=SYSTEM_PYTHON,
            analysis_sys_path=manager._resolve_analysis_sys_path(SYSTEM_PYTHON),
            project_root=os.getcwd(),
            target="mutagen.File",
            prefix="",
        )
        labels = {str(item.get("label") or "") for item in items}

        self.assertTrue({"clear", "info", "tags", "values"}.issubset(labels))

    def test_assignment_based_attribute_resolves_back_to_dynamic_callable(self) -> None:
        source = """import os
import mutagen
for root, dirs, files in os.walk(args.root):
    for file in files:
        if file.endswith(('.mp3', '.wav', '.flac')):
            file_path = os.path.join(root, file)
            audio = mutagen.File(file_path)
            if audio is not None:
                print(audio.)
"""
        context = _detect_context(source, 9, len("                print(audio."), "")

        self.assertEqual(context.get("attribute_expr"), "audio")
        self.assertEqual(context.get("callable_target"), "")
        resolved = _resolve_attribute_callable_target(source, context)
        self.assertEqual(resolved, "mutagen.File")
        augmented_context = {**context, "resolved_callable_target": resolved}
        self.assertTrue(
            _should_augment_dynamic_attribute_items(
                [
                    {"label": "__bool__", "insert_text": "__bool__"},
                    {"label": "__dir__", "insert_text": "__dir__"},
                ],
                augmented_context,
            )
        )


if __name__ == "__main__":
    unittest.main()
