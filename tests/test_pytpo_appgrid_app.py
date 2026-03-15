from __future__ import annotations

import unittest

from pytpo_appgrid.app import _should_suppress_qt_message


class AppGridAppTests(unittest.TestCase):
    def test_svg_property_warning_is_suppressed(self) -> None:
        self.assertTrue(_should_suppress_qt_message("qt.svg", "Could not resolve property: #gradient"))

    def test_other_qt_messages_are_not_suppressed(self) -> None:
        self.assertFalse(_should_suppress_qt_message("qt.qpa", "Could not load platform plugin"))
        self.assertFalse(_should_suppress_qt_message("qt.svg", "Different warning"))


if __name__ == "__main__":
    unittest.main()
