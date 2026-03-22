from __future__ import annotations

import unittest

from PySide6.QtGui import QColor

from topbar.appearance import apply_color_opacity


class TopBarAppearanceTests(unittest.TestCase):
    def test_apply_color_opacity_preserves_embedded_alpha(self) -> None:
        color = QColor(10, 20, 30, 128)
        adjusted = apply_color_opacity(color, 50)
        self.assertEqual(adjusted.alpha(), 64)


if __name__ == "__main__":
    unittest.main()
