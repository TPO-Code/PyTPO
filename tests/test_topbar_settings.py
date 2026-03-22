from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QSpinBox

from topbar.settings import TopBarBehaviorSettings, TopBarSettingsBackend
from topbar.settings_dialog import TopBarSettingsDialog


class TopBarSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_behavior_settings_normalize_invalid_values(self) -> None:
        settings = TopBarBehaviorSettings.from_mapping(
            {
                "auto_hide": "yes",
                "auto_hide_effect_fade": "1",
                "auto_hide_reveal_delay_ms": -50,
                "auto_hide_hide_delay_ms": 99999,
                "auto_hide_animation_duration_ms": "180",
                "auto_hide_reveal_distance_px": 0,
                "auto_hide_expand_origin": "diagonal",
                "auto_hide_expand_initial_width_percent": 105,
                "auto_hide_show_easing": "ease_in_out",
                "auto_hide_hide_easing": "bounce",
                "appearance_background_type": "video",
                "appearance_background_color": "not-a-color",
                "appearance_gradient_direction": "spiral",
                "appearance_image_fit_mode": "zoom",
                "appearance_height": 999,
                "appearance_button_background_style": "glass",
                "appearance_tray_button_style": "neon",
                "menu_appearance_panel_width": 1200,
                "menu_appearance_scrollbar_visibility": "sometimes",
                "menu_appearance_background_type": "lava",
                "media_controls_interaction_mode": "transport",
                "media_cards_background_type": "video",
                "media_cards_button_size": 4,
            }
        )

        self.assertTrue(settings.auto_hide)
        self.assertTrue(settings.auto_hide_effect_fade)
        self.assertEqual(settings.auto_hide_reveal_delay_ms, 0)
        self.assertEqual(settings.auto_hide_hide_delay_ms, 5000)
        self.assertEqual(settings.auto_hide_animation_duration_ms, 180)
        self.assertEqual(settings.auto_hide_reveal_distance_px, 1)
        self.assertEqual(settings.auto_hide_expand_origin, "center")
        self.assertEqual(settings.auto_hide_expand_initial_width_percent, 100)
        self.assertEqual(settings.auto_hide_show_easing, "ease_in_out")
        self.assertEqual(settings.auto_hide_hide_easing, "ease_in")
        self.assertEqual(settings.appearance_background_type, "solid")
        self.assertEqual(settings.appearance_background_color, "#5b5b5b")
        self.assertEqual(settings.appearance_gradient_direction, "horizontal")
        self.assertEqual(settings.appearance_image_fit_mode, "cover")
        self.assertEqual(settings.appearance_height, 96)
        self.assertEqual(settings.appearance_button_background_style, "subtle")
        self.assertEqual(settings.appearance_tray_button_style, "match_buttons")
        self.assertEqual(settings.menu_appearance_panel_width, 960)
        self.assertEqual(settings.menu_appearance_scrollbar_visibility, "auto")
        self.assertEqual(settings.menu_appearance_background_type, "solid")
        self.assertEqual(settings.media_controls_interaction_mode, "full_media_controls")
        self.assertEqual(settings.media_cards_background_type, "solid")
        self.assertEqual(settings.media_cards_button_size, 18)

    def test_backend_preserves_extra_dialog_state_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "topbar-settings.json"
            backend = TopBarSettingsBackend(path)

            backend.set("auto_hide", True, "topbar")
            backend.set("ui.topbar.settings_dialog.tree_expanded_paths", [["Topbar", "Behavior"]], "topbar")
            backend.save_all(scopes={"topbar"})

            reloaded = TopBarSettingsBackend(path)
            self.assertTrue(reloaded.get("auto_hide", "topbar", default=False))
            self.assertEqual(
                reloaded.get("ui.topbar.settings_dialog.tree_expanded_paths", "topbar", default=[]),
                [["Topbar", "Behavior"]],
            )

    def test_settings_dialog_hides_dependent_controls_until_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "topbar-settings.json"
            dialog = TopBarSettingsDialog(backend=TopBarSettingsBackend(path))
            auto_hide = dialog.findChild(QCheckBox, "SettingsField__topbar__auto_hide")
            expand_width = dialog.findChild(QCheckBox, "SettingsField__topbar__auto_hide_effect_expand_width")
            reveal_delay = dialog.findChild(QSpinBox, "SettingsField__topbar__auto_hide_reveal_delay_ms")
            initial_width = dialog.findChild(QSpinBox, "SettingsField__topbar__auto_hide_expand_initial_width_percent")
            expand_origin = dialog.findChild(QComboBox, "SettingsField__topbar__auto_hide_expand_origin")

            self.assertIsNotNone(auto_hide)
            self.assertIsNotNone(expand_width)
            self.assertIsNotNone(reveal_delay)
            self.assertIsNotNone(initial_width)
            self.assertIsNotNone(expand_origin)
            self.assertTrue(reveal_delay.isHidden())
            self.assertTrue(initial_width.isHidden())
            self.assertTrue(expand_origin.isHidden())

            auto_hide.setChecked(True)
            self._app.processEvents()
            self.assertFalse(reveal_delay.isHidden())
            self.assertTrue(initial_width.isHidden())
            self.assertTrue(expand_origin.isHidden())

            expand_width.setChecked(True)
            self._app.processEvents()
            self.assertFalse(initial_width.isHidden())
            self.assertFalse(expand_origin.isHidden())
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
