from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pytpo_appgrid.settings import AppGridSettingsBackend, AppGridVisualSettings


class AppGridSettingsTests(unittest.TestCase):
    def test_defaults_are_loaded_when_settings_file_is_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            backend = AppGridSettingsBackend(Path(tmpdir) / "settings.json")
            settings = AppGridVisualSettings.from_mapping(backend.defaults)

            self.assertEqual(settings.icon_size, 52)
            self.assertEqual(settings.tile_spacing, 10)
            self.assertEqual(settings.window.background_image_fit, "cover")

    def test_backend_persists_customized_values(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            backend = AppGridSettingsBackend(path)
            backend.set("highlight_color", "#123456ff", "appgrid")
            backend.set("icon_size", 80, "appgrid")
            backend.save_all()

            reloaded = AppGridSettingsBackend(path)

            self.assertEqual(reloaded.get("highlight_color"), "#123456ff")
            self.assertEqual(reloaded.get("icon_size"), 80)


if __name__ == "__main__":
    unittest.main()
