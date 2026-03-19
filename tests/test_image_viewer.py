from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pytpo.ui.widgets.image_viewer import ImageViewerWidget


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class ImageViewerWidgetTests(unittest.TestCase):
    def test_svg_loads_as_vector_and_refreshes_dimensions(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "icon.svg"
            path.write_text(
                (
                    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="10" '
                    'viewBox="0 0 12 10"><rect width="12" height="10" fill="#000"/></svg>'
                ),
                encoding="utf-8",
            )

            viewer = ImageViewerWidget()
            self.addCleanup(viewer.deleteLater)

            self.assertTrue(viewer.load_file(str(path)))
            self.assertTrue(viewer.is_vector_image())
            self.assertIn("12x10 px", viewer._status_label.text())
            self.assertIn("vector", viewer._status_label.text())

            path.write_text(
                (
                    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="18" '
                    'viewBox="0 0 24 18"><rect width="24" height="18" fill="#000"/></svg>'
                ),
                encoding="utf-8",
            )

            self.assertTrue(viewer.load_file(str(path)))
            self.assertIn("24x18 px", viewer._status_label.text())


if __name__ == "__main__":
    unittest.main()
