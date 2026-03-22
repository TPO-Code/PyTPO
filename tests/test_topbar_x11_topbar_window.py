from __future__ import annotations

import unittest

from PySide6.QtCore import QRect

from topbar.x11_topbar_window import build_top_strut_reservation


class TopBarX11WindowTests(unittest.TestCase):
    def test_build_top_strut_reservation_uses_visible_top_band(self) -> None:
        reservation = build_top_strut_reservation(
            window_rect=QRect(0, 0, 1920, 35),
            screen_rect=QRect(0, 0, 1920, 1080),
            reserve_height=35,
        )

        self.assertEqual(reservation.strut, (0, 0, 35, 0))
        self.assertEqual(reservation.strut_partial, (0, 0, 35, 0, 0, 0, 0, 0, 0, 1919, 0, 0))

    def test_build_top_strut_reservation_clears_when_not_reserved(self) -> None:
        reservation = build_top_strut_reservation(
            window_rect=QRect(0, -35, 1920, 35),
            screen_rect=QRect(0, 0, 1920, 1080),
            reserve_height=0,
        )

        self.assertEqual(reservation.strut, (0, 0, 0, 0))
        self.assertEqual(reservation.strut_partial, (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
