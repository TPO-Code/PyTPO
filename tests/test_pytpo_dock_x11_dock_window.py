from __future__ import annotations

import unittest

from PySide6.QtCore import QRect

from pytpo_dock.x11_dock_window import build_bottom_strut_reservation


class X11DockWindowTests(unittest.TestCase):
    def test_build_bottom_strut_reservation_uses_visible_bottom_band(self) -> None:
        reservation = build_bottom_strut_reservation(
            window_rect=QRect(760, 1025, 400, 40),
            screen_rect=QRect(0, 0, 1920, 1080),
            reserve_space=True,
        )

        self.assertEqual(reservation.strut, (0, 0, 0, 55))
        self.assertEqual(reservation.strut_partial, (0, 0, 0, 55, 0, 0, 0, 0, 0, 0, 760, 1159))

    def test_build_bottom_strut_reservation_clears_when_not_reserved(self) -> None:
        reservation = build_bottom_strut_reservation(
            window_rect=QRect(760, 1025, 400, 40),
            screen_rect=QRect(0, 0, 1920, 1080),
            reserve_space=False,
        )

        self.assertEqual(reservation.strut, (0, 0, 0, 0))
        self.assertEqual(reservation.strut_partial, (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
