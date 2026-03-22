from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QScrollArea

from topbar.settings import TopBarBehaviorSettings
from topbar.system_menu import TopBarSystemMenuPanel
from topbar.system_menu.footer import FooterSection
from topbar.system_menu.media_container import MediaContainer
from topbar.system_menu.service import MediaSnapshot, PlayerInfo


class TopBarSystemMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_media_container_is_not_scrollable_itself(self) -> None:
        container = MediaContainer()
        try:
            self.assertEqual(container.findChildren(QScrollArea), [])
        finally:
            container.deleteLater()

    def test_media_container_hides_when_media_section_disabled(self) -> None:
        container = MediaContainer()
        try:
            container.apply_settings(TopBarBehaviorSettings(media_controls_show_media_players=False))
            container.apply_snapshot(
                MediaSnapshot(
                    playerctl_missing=False,
                    gdbus_missing=False,
                    players=(PlayerInfo(name="vlc", identity="VLC", status="Playing", title="Song"),),
                )
            )
            self.assertFalse(container.isVisible())
        finally:
            container.deleteLater()

    def test_system_menu_panel_uses_outer_scroll_area(self) -> None:
        panel = TopBarSystemMenuPanel(
            open_terminal=lambda: None,
            open_dock=lambda: None,
            open_settings=lambda: None,
        )
        try:
            scroll_areas = panel.findChildren(QScrollArea)
            self.assertEqual(len(scroll_areas), 1)
            self.assertIs(scroll_areas[0].widget(), panel.content)
            self.assertFalse(scroll_areas[0].viewport().autoFillBackground())
            self.assertFalse(panel.content.autoFillBackground())
        finally:
            panel.content._shutdown()
            panel.deleteLater()

    def test_footer_power_menu_includes_restart(self) -> None:
        footer = FooterSection(
            open_terminal=lambda: None,
            open_dock=lambda: None,
            open_settings=lambda: None,
            close_panel=lambda: None,
        )
        try:
            actions = [action.text() for action in footer.power_menu.actions() if action.text()]
            self.assertIn("Restart", actions)
        finally:
            footer.deleteLater()


if __name__ == "__main__":
    unittest.main()
