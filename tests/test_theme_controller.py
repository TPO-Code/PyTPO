from __future__ import annotations

import unittest

from pytpo.ui.controllers.theme_controller import ThemeController


class ThemeControllerTests(unittest.TestCase):
    def test_global_stylesheet_overrides_hide_scrollbar_buttons(self) -> None:
        styled = ThemeController._append_global_stylesheet_overrides("QWidget { color: white; }")

        self.assertIn("QScrollBar:vertical", styled)
        self.assertIn("margin-top: 0px;", styled)
        self.assertIn("QScrollBar::add-line:vertical", styled)
        self.assertIn("height: 0px;", styled)
        self.assertIn("QScrollBar::up-arrow:vertical", styled)

    def test_global_stylesheet_overrides_are_not_duplicated(self) -> None:
        once = ThemeController._append_global_stylesheet_overrides("QWidget { color: white; }")
        twice = ThemeController._append_global_stylesheet_overrides(once)

        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
