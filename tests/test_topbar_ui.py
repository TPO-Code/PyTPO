from __future__ import annotations

import unittest

from PySide6.QtCore import QEasingCurve, QRect

from topbar.settings import TopBarBehaviorSettings
from topbar.ui import TopBar


class TopBarUiHelpersTests(unittest.TestCase):
    def test_hidden_width_uses_configured_expand_percent(self) -> None:
        class _FakeTopBar:
            def __init__(self) -> None:
                self._behavior_settings = TopBarBehaviorSettings(auto_hide_expand_initial_width_percent=50)

        width = TopBar._hidden_width_for_screen(_FakeTopBar(), QRect(0, 0, 1920, 1080))
        self.assertEqual(width, 960)

    def test_hidden_animation_geometry_stays_on_screen_without_slide(self) -> None:
        class _FakeTopBar:
            def __init__(self) -> None:
                self._behavior_settings = TopBarBehaviorSettings(
                    auto_hide_effect_expand_width=True,
                    auto_hide_effect_slide=False,
                )

            def _hidden_geometry_for_screen(self, _screen_rect: QRect) -> QRect:
                return QRect(140, -35, 1640, 35)

        geometry = TopBar._hidden_animation_geometry_for_screen(_FakeTopBar(), QRect(0, 0, 1920, 1080))
        self.assertEqual(geometry.top(), 0)
        self.assertEqual(geometry.width(), 1640)

    def test_auto_hide_never_reserves_screen_space(self) -> None:
        class _FakeTopBar:
            def __init__(self) -> None:
                self._behavior_settings = TopBarBehaviorSettings(reserve_screen_space=True)
                self._auto_hide_enabled = True
                self._is_hidden_to_edge = False
                self._visible_reserve_height = 0

            def height(self) -> int:
                return 35

        self.assertEqual(TopBar._current_reserved_height(_FakeTopBar()), 0)

    def test_animation_easing_uses_separate_show_and_hide_settings(self) -> None:
        fake = type(
            "_FakeTopBar",
            (),
            {
                "_behavior_settings": TopBarBehaviorSettings(
                    auto_hide_show_easing="ease_in_out",
                    auto_hide_hide_easing="linear",
                )
            },
        )()

        self.assertEqual(TopBar._animation_easing_curve(fake, hidden=False), QEasingCurve.InOutCubic)
        self.assertEqual(TopBar._animation_easing_curve(fake, hidden=True), QEasingCurve.Linear)


if __name__ == "__main__":
    unittest.main()
