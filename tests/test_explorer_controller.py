from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from barley_ide.git.workspace_repository_index import WorkspaceRepositoryIndex
from barley_ide.ui.controllers.explorer_controller import ExplorerController


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, text: str, _timeout: int = 0) -> None:
        self.messages.append(str(text))


class _FakeTree:
    def __init__(self) -> None:
        self.refreshed_paths: list[str] = []
        self.selected_paths: list[str] = []
        self.current_path: str | None = None

    def refresh_subtree(self, path: str) -> None:
        self.refreshed_paths.append(str(path))

    def select_path(self, path: str) -> None:
        self.selected_paths.append(str(path))
        self.current_path = str(path)

    def selected_path(self) -> str | None:
        return self.current_path


class _FakeIde:
    def __init__(self, project_root: str) -> None:
        self.project_root = str(Path(project_root).resolve())
        self._status_bar = _StatusBar()
        self.git_refresh_delays: list[int] = []
        self.workspace_repository_index = WorkspaceRepositoryIndex.discover(self.project_root)

    def statusBar(self) -> _StatusBar:
        return self._status_bar

    def is_project_read_only(self) -> bool:
        return False

    def _canonical_path(self, path: str) -> str:
        return str(Path(path).resolve())

    def schedule_git_status_refresh(self, *, delay_ms: int = 0) -> None:
        self.git_refresh_delays.append(int(delay_ms))

    def refresh_workspace_repository_index(self, *, update_tree: bool = True) -> WorkspaceRepositoryIndex:
        self.workspace_repository_index = WorkspaceRepositoryIndex.discover(self.project_root)
        return self.workspace_repository_index


class ExplorerControllerTests(unittest.TestCase):
    def test_selection_context_distinguishes_project_repo_and_path_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "apps" / "app1"
            repo_root.mkdir(parents=True)
            (repo_root / ".git").mkdir()
            file_path = repo_root / "src" / "main.py"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("print('hi')\n", encoding="utf-8")

            ide = _FakeIde(tmpdir)
            ide.workspace_repository_index = WorkspaceRepositoryIndex.discover(str(root))
            tree = _FakeTree()
            controller = ExplorerController(ide, tree)

            tree.current_path = str(root.resolve())
            project_context = controller.current_selection_context()
            self.assertEqual(project_context.scope_kind, "project")
            self.assertIsNone(project_context.repo_root)
            self.assertTrue(project_context.requires_repo_choice)

            tree.current_path = str(repo_root.resolve())
            repo_context = controller.current_selection_context()
            self.assertEqual(repo_context.scope_kind, "repo")
            self.assertEqual(repo_context.repo_root, str(repo_root.resolve()))
            self.assertFalse(repo_context.requires_repo_choice)

            tree.current_path = str(file_path.resolve())
            path_context = controller.current_selection_context()
            self.assertEqual(path_context.scope_kind, "path")
            self.assertEqual(path_context.repo_root, str(repo_root.resolve()))
            self.assertFalse(path_context.requires_repo_choice)

    def test_selection_context_uses_project_root_repo_when_workspace_is_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            child_repo = root / "apps" / "app1"
            child_repo.mkdir(parents=True)
            (child_repo / ".git").mkdir()

            ide = _FakeIde(tmpdir)
            ide.workspace_repository_index = WorkspaceRepositoryIndex.discover(str(root))
            tree = _FakeTree()
            tree.current_path = str(root.resolve())
            controller = ExplorerController(ide, tree)

            context = controller.current_selection_context()
            self.assertEqual(context.scope_kind, "project")
            self.assertEqual(context.repo_root, str(root.resolve()))
            self.assertFalse(context.requires_repo_choice)

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
