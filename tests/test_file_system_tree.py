from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from pytpo.ui.widgets.file_system_tree import FileSystemTreeWidget


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class FileSystemTreeWidgetTests(unittest.TestCase):
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
