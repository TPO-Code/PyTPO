from __future__ import annotations

import unittest

from pytpo_appgrid.theme import load_default_stylesheet


class AppGridThemeTests(unittest.TestCase):
    def test_default_stylesheet_loads(self) -> None:
        stylesheet = load_default_stylesheet()
        self.assertTrue(stylesheet.strip())
        self.assertIn("QWidget", stylesheet)


if __name__ == "__main__":
    unittest.main()
