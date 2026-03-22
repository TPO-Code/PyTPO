from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtWidgets import QApplication

from pytpo.git.workspace_repository_index import WorkspaceRepositoryIndex
from pytpo.ui.widgets.file_system_tree import FileSystemTreeWidget


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class FileSystemTreeWidgetTests(unittest.TestCase):
    def test_project_root_is_visible_and_repo_roots_are_marked(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "apps" / "app1"
            package_root = root / "packages" / "shared"
            normal_folder = root / "notes"
            repo_root.mkdir(parents=True)
            package_root.mkdir(parents=True)
            normal_folder.mkdir()
            (repo_root / ".git").mkdir()
            (package_root / ".git").write_text("gitdir: ../.git/modules/shared\n", encoding="utf-8")

            tree = FileSystemTreeWidget(str(root))
            tree.set_root_display_name("Workspace")
            tree.set_workspace_repository_index(WorkspaceRepositoryIndex.discover(str(root)))
            tree.show()
            app.processEvents()

            model = tree.model()
            self.assertEqual(model.rowCount(QModelIndex()), 1)

            project_index = model.index(0, 0, QModelIndex())
            self.assertTrue(project_index.isValid())
            self.assertEqual(model.data(project_index), "[Workspace]")
            project_meta = tree.metadata_for_path(str(root))
            self.assertTrue(project_meta["is_project_root"])
            self.assertEqual(project_meta["scope_kind"], "project")

            tree.expand(project_index)
            app.processEvents()

            repo_meta = tree.metadata_for_path(str(repo_root))
            package_meta = tree.metadata_for_path(str(package_root))
            normal_meta = tree.metadata_for_path(str(normal_folder))
            self.assertTrue(repo_meta["is_repo_root"])
            self.assertEqual(repo_meta["owning_repo_root"], str(repo_root.resolve()))
            self.assertEqual(repo_meta["repo_kind"], "repo")
            self.assertTrue(package_meta["is_repo_root"])
            self.assertEqual(package_meta["repo_kind"], "linked")
            self.assertFalse(normal_meta["is_repo_root"])

            self.assertTrue(model.ensure_path_visible(str(repo_root)))
            self.assertTrue(model.ensure_path_visible(str(package_root)))
            repo_index = model.index_from_path(str(repo_root))
            package_index = model.index_from_path(str(package_root))
            normal_index = model.index_from_path(str(normal_folder))
            repo_icon = model.data(repo_index, role=Qt.ItemDataRole.DecorationRole)
            package_icon = model.data(package_index, role=Qt.ItemDataRole.DecorationRole)
            normal_icon = model.data(normal_index, role=Qt.ItemDataRole.DecorationRole)
            self.assertFalse(repo_icon.pixmap(20, 20).isNull())
            self.assertFalse(package_icon.pixmap(20, 20).isNull())
            self.assertFalse(normal_icon.pixmap(20, 20).isNull())
            self.assertNotEqual(repo_icon.pixmap(20, 20).cacheKey(), normal_icon.pixmap(20, 20).cacheKey())
            self.assertNotEqual(package_icon.pixmap(20, 20).cacheKey(), normal_icon.pixmap(20, 20).cacheKey())
            self.assertNotEqual(package_icon.pixmap(20, 20).cacheKey(), repo_icon.pixmap(20, 20).cacheKey())
            self.assertIn("Linked Git repository / package", str(model.data(package_index, role=Qt.ItemDataRole.ToolTipRole) or ""))

            tree.close()

    def test_bottom_of_viewport_keeps_empty_context_menu_gap(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for index in range(80):
                (root / f"file_{index:03d}.txt").write_text("x\n", encoding="utf-8")

            tree = FileSystemTreeWidget(str(root))
            tree.resize(280, 220)
            tree.show()
            app.processEvents()

            vbar = tree.verticalScrollBar()
            self.assertGreater(vbar.maximum(), 0)

            vbar.setValue(vbar.maximum())
            app.processEvents()

            point = QPoint(12, max(0, tree.viewport().height() - 2))
            self.assertFalse(tree.indexAt(point).isValid())

            tree.close()


if __name__ == "__main__":
    unittest.main()
