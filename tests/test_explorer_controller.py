from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pytpo.ui.controllers.explorer_controller import ExplorerController


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, text: str, _timeout: int = 0) -> None:
        self.messages.append(str(text))


class _FakeTree:
    def __init__(self) -> None:
        self.refreshed_paths: list[str] = []
        self.selected_paths: list[str] = []

    def refresh_subtree(self, path: str) -> None:
        self.refreshed_paths.append(str(path))

    def select_path(self, path: str) -> None:
        self.selected_paths.append(str(path))


class _FakeIde:
    def __init__(self, project_root: str) -> None:
        self.project_root = str(Path(project_root).resolve())
        self._status_bar = _StatusBar()
        self.git_refresh_delays: list[int] = []

    def statusBar(self) -> _StatusBar:
        return self._status_bar

    def is_project_read_only(self) -> bool:
        return False

    def _canonical_path(self, path: str) -> str:
        return str(Path(path).resolve())

    def schedule_git_status_refresh(self, *, delay_ms: int = 0) -> None:
        self.git_refresh_delays.append(int(delay_ms))


class ExplorerControllerTests(unittest.TestCase):
    def test_create_new_folder_accepts_nested_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ide = _FakeIde(tmpdir)
            tree = _FakeTree()
            controller = ExplorerController(ide, tree)
            controller._prompt_simple_name = lambda *args, **kwargs: "docs/images"

            controller._create_new_folder(tmpdir)

            created = Path(tmpdir) / "docs" / "images"
            self.assertTrue(created.is_dir())
            self.assertEqual(tree.refreshed_paths, [str(Path(tmpdir).resolve())])
            self.assertEqual(tree.selected_paths, [str(created.resolve())])
            self.assertEqual(ide.git_refresh_delays, [120])
            self.assertIn("Created folder: docs/images", ide.statusBar().messages)


if __name__ == "__main__":
    unittest.main()
