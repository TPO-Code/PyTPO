from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QApplication

from barley_ide.git.workspace_repository_index import WorkspaceRepositoryIndex
from barley_ide.ui.widgets.file_system_tree import FileSystemTreeWidget


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

    def test_folder_tint_ignores_hidden_untracked_descendants(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "src"
            hidden_dir = src / ".cache"
            tracked_file = src / "main.py"
            hidden_untracked = hidden_dir / "scratch.py"
            hidden_dir.mkdir(parents=True)
            tracked_file.write_text("print('ok')\n", encoding="utf-8")
            hidden_untracked.write_text("print('tmp')\n", encoding="utf-8")

            tree = FileSystemTreeWidget(
                str(root),
                exclude_path_predicate=lambda path, _is_dir: Path(path).name.startswith("."),
            )
            tree.set_git_tinting(
                enabled=True,
                colors={
                    "clean": "#7fbf7f",
                    "dirty": "#e69f6b",
                    "untracked": "#c8c8c8",
                },
            )
            tree.set_git_status_maps(
                file_states={
                    str(tracked_file): "clean",
                    str(hidden_untracked): "untracked",
                },
                folder_states={
                    str(root): "untracked",
                    str(src): "untracked",
                    str(hidden_dir): "untracked",
                },
            )
            tree.show()
            app.processEvents()

            src_index = tree.model().index_from_path(str(src))
            self.assertTrue(src_index.isValid())
            self.assertEqual(tree.metadata_for_path(str(src))["git_state"], "clean")

            brush = tree.model().data(src_index, role=Qt.ItemDataRole.ForegroundRole)
            self.assertIsInstance(brush, QBrush)
            self.assertEqual(brush.color().name().lower(), "#7fbf7f")

            tree.close()

    def test_reapplying_same_git_state_does_not_emit_tree_wide_updates(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tracked_file = root / "main.py"
            tracked_file.write_text("print('ok')\n", encoding="utf-8")

            tree = FileSystemTreeWidget(str(root))
            tree.set_git_tinting(
                enabled=True,
                colors={
                    "clean": "#7fbf7f",
                    "dirty": "#e69f6b",
                    "untracked": "#c8c8c8",
                },
            )
            tree.show()
            app.processEvents()

            changes: list[tuple[int, int]] = []
            tree.model().dataChanged.connect(
                lambda top_left, bottom_right, _roles: changes.append((top_left.row(), bottom_right.row()))
            )

            state_payload = {
                "file_states": {str(tracked_file): "clean"},
                "folder_states": {str(root): "clean"},
            }

            tree.set_git_status_maps(**state_payload)
            app.processEvents()
            self.assertTrue(changes)

            changes.clear()
            tree.set_git_status_maps(**state_payload)
            app.processEvents()
            self.assertEqual(changes, [])

            tree.close()

    def test_refresh_subtree_only_restores_expansion_within_target_branch(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src_pkg = root / "src" / "pkg"
            other_leaf = root / "other" / "leaf"
            src_pkg.mkdir(parents=True)
            other_leaf.mkdir(parents=True)
            (src_pkg / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (other_leaf / "note.txt").write_text("x\n", encoding="utf-8")

            tree = FileSystemTreeWidget(str(root))
            tree.show()
            app.processEvents()

            project_index = tree.model().index(0, 0, QModelIndex())
            tree.expand(project_index)
            app.processEvents()

            model = tree.model()
            src_index = model.index_from_path(str(root / "src"))
            other_index = model.index_from_path(str(root / "other"))
            self.assertTrue(src_index.isValid())
            self.assertTrue(other_index.isValid())

            tree.expand(src_index)
            tree.expand(other_index)
            app.processEvents()

            pkg_index = model.index_from_path(str(src_pkg))
            self.assertTrue(pkg_index.isValid())
            tree.expand(pkg_index)
            app.processEvents()

            tree.refresh_subtree(str(root / "src"))
            app.processEvents()

            src_index = model.index_from_path(str(root / "src"))
            other_index = model.index_from_path(str(root / "other"))
            pkg_index = model.index_from_path(str(src_pkg))
            self.assertTrue(tree.isExpanded(src_index))
            self.assertTrue(tree.isExpanded(other_index))
            self.assertTrue(tree.isExpanded(pkg_index))

            tree.close()

    def test_refresh_subtree_for_project_root_does_not_reset_entire_model(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src_pkg = root / "src" / "pkg"
            docs = root / "docs"
            src_pkg.mkdir(parents=True)
            docs.mkdir()
            (src_pkg / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (docs / "note.txt").write_text("x\n", encoding="utf-8")

            tree = FileSystemTreeWidget(str(root))
            tree.show()
            app.processEvents()

            model = tree.model()
            project_index = model.index(0, 0, QModelIndex())
            tree.expand(project_index)
            app.processEvents()

            src_index = model.index_from_path(str(root / "src"))
            self.assertTrue(src_index.isValid())
            tree.expand(src_index)
            app.processEvents()

            model_resets: list[None] = []
            rows_removed: list[tuple[int, int]] = []
            rows_inserted: list[tuple[int, int]] = []
            model.modelReset.connect(lambda: model_resets.append(None))
            model.rowsRemoved.connect(lambda _parent, first, last: rows_removed.append((first, last)))
            model.rowsInserted.connect(lambda _parent, first, last: rows_inserted.append((first, last)))

            (root / "new_root_file.txt").write_text("root change\n", encoding="utf-8")
            tree.refresh_subtree(str(root))
            app.processEvents()

            self.assertEqual(model_resets, [])
            self.assertTrue(rows_inserted)
            self.assertTrue(tree.isExpanded(model.index_from_path(str(root / "src"))))

            tree.close()

    def test_refresh_subtree_for_loaded_folder_reloads_children_in_place(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src_pkg = root / "src" / "pkg"
            other_dir = root / "other"
            src_pkg.mkdir(parents=True)
            other_dir.mkdir()
            (src_pkg / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "src" / "notes.txt").write_text("x\n", encoding="utf-8")

            tree = FileSystemTreeWidget(str(root))
            tree.show()
            app.processEvents()

            model = tree.model()
            project_index = model.index(0, 0, QModelIndex())
            tree.expand(project_index)
            app.processEvents()

            src_path = str(root / "src")
            src_index = model.index_from_path(src_path)
            self.assertTrue(src_index.isValid())
            tree.expand(src_index)
            app.processEvents()

            removed_parents: list[str | None] = []
            inserted_parents: list[str | None] = []
            model.rowsRemoved.connect(
                lambda parent, _first, _last: removed_parents.append(tree.path_from_index(parent))
            )
            model.rowsInserted.connect(
                lambda parent, _first, _last: inserted_parents.append(tree.path_from_index(parent))
            )

            (root / "src" / "added.txt").write_text("new\n", encoding="utf-8")
            tree.refresh_subtree(src_path)
            app.processEvents()

            self.assertTrue(inserted_parents)
            self.assertIn(src_path, inserted_parents)
            self.assertNotIn(str(root), inserted_parents)
            self.assertNotIn(src_path, removed_parents)

            tree.close()

    def test_reapplying_same_git_tint_config_does_not_emit_tree_wide_updates(self) -> None:
        app = _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")

            tree = FileSystemTreeWidget(str(root))
            tree.show()
            app.processEvents()

            changes: list[tuple[int, int]] = []
            tree.model().dataChanged.connect(
                lambda top_left, bottom_right, _roles: changes.append((top_left.row(), bottom_right.row()))
            )

            tint = {
                "clean": "#7fbf7f",
                "dirty": "#e69f6b",
                "untracked": "#c8c8c8",
            }

            tree.set_git_tinting(enabled=True, colors=tint)
            app.processEvents()
            self.assertTrue(changes)

            changes.clear()
            tree.set_git_tinting(enabled=True, colors=tint)
            app.processEvents()
            self.assertEqual(changes, [])

            tree.close()


if __name__ == "__main__":
    unittest.main()
