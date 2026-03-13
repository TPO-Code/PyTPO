import importlib.util
import os
import sys
import tempfile
import types
import unittest

from pytpo.services.ast_query import common_symbol_imports


def _load_diagnostics_controller_class():
    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.QRect = type("QRect", (), {})
    qtcore.Qt = type("Qt", (), {})

    qtgui = types.ModuleType("PySide6.QtGui")
    for name in ("QColor", "QCursor", "QFont", "QImage", "QPainter", "QPen", "QTextCursor"):
        setattr(qtgui, name, type(name, (), {}))

    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    qtwidgets.QMenu = type("QMenu", (), {})

    pyside6 = types.ModuleType("PySide6")
    pyside6.QtCore = qtcore
    pyside6.QtGui = qtgui
    pyside6.QtWidgets = qtwidgets

    editor_workspace = types.ModuleType("pytpo.ui.editor_workspace")
    editor_workspace.EditorWidget = type("EditorWidget", (), {})

    tdoc_support = types.ModuleType("TPOPyside.widgets.tdoc_support")
    tdoc_support.PROJECT_MARKER_FILENAME = ".tdocproject"
    tdoc_support.TDocProjectIndex = type("TDocProjectIndex", (), {})
    tdoc_support.parse_file_link = lambda value: ("", 0)
    tdoc_support.resolve_tdoc_root_for_path = lambda path, project_root="": project_root or path

    saved = {}
    for name, module in {
        "PySide6": pyside6,
        "PySide6.QtCore": qtcore,
        "PySide6.QtGui": qtgui,
        "PySide6.QtWidgets": qtwidgets,
        "pytpo.ui.editor_workspace": editor_workspace,
        "TPOPyside.widgets.tdoc_support": tdoc_support,
    }.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = module

    try:
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "src",
            "ui",
            "controllers",
            "diagnostics_controller.py",
        )
        spec = importlib.util.spec_from_file_location("test_diagnostics_controller", path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module.DiagnosticsController
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


DiagnosticsController = _load_diagnostics_controller_class()


class _DummyIDE:
    def _canonical_path(self, path: str) -> str:
        return os.path.abspath(path)

    def _path_has_prefix(self, path: str, prefix: str) -> bool:
        try:
            return os.path.commonpath([self._canonical_path(path), self._canonical_path(prefix)]) == self._canonical_path(prefix)
        except Exception:
            return False

    def is_path_excluded(self, _path: str, *, for_feature: str = "") -> bool:
        return False

    def _rel_to_project(self, path: str) -> str:
        return os.path.relpath(self._canonical_path(path), self.project_root)

    def _normalize_rel(self, rel_path: str) -> str:
        return str(rel_path or "").replace(os.sep, "/")


class _DummyProjectContext:
    def __init__(self, project_root: str):
        self.project_root = project_root

    def lint_follow_symlinks(self) -> bool:
        return False


class PythonImportQuickFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.project_root = self._tmpdir.name
        self.ide = _DummyIDE()
        self.ide.project_root = self.project_root
        self.controller = DiagnosticsController(self.ide, _DummyProjectContext(self.project_root))
        self.file_path = os.path.join(self.project_root, "example.py")

    def test_dataclass_resolves_to_dataclasses_import(self) -> None:
        self.assertEqual(common_symbol_imports("dataclass"), [("dataclasses", "dataclass")])
        source = "import argparse\n\n@dataclass\nclass Example:\n    pass\n"
        candidates = self.controller._resolve_import_candidates(
            symbol="dataclass",
            source_text=source,
            prefer_module_import=False,
            current_file_path=self.file_path,
        )

        self.assertIn(
            {
                "kind": "from_import",
                "module": "dataclasses",
                "name": "dataclass",
                "bind": "dataclass",
                "label": "from dataclasses import dataclass",
                "source_kind": "common_symbol",
                "in_file": False,
            },
            candidates,
        )

    def test_module_style_symbol_gets_heuristic_import_candidate(self) -> None:
        self.controller._can_import_module = lambda _module_name: False
        source = "print(mutagen.File('track.mp3'))\n"
        candidates = self.controller._resolve_import_candidates(
            symbol="mutagen",
            source_text=source,
            prefer_module_import=True,
            current_file_path=self.file_path,
        )

        self.assertIn(
            {
                "kind": "import_module",
                "module": "mutagen",
                "name": "",
                "bind": "mutagen",
                "label": "import mutagen",
                "source_kind": "heuristic_module",
                "in_file": False,
            },
            candidates,
        )


if __name__ == "__main__":
    unittest.main()
