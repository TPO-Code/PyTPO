from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from pytpo_dock.autostart import DockAutostartManager
from pytpo_dock.settings_dialog import DockAutostartFieldController


class DockAutostartTests(unittest.TestCase):
    def test_manager_enable_and_disable_follow_xdg_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            xdg_config_home = Path(tmpdir) / "xdg-config"
            manager = DockAutostartManager()

            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_config_home)}, clear=False):
                path = manager.enable()

                self.assertEqual(path, xdg_config_home / "autostart" / "pytpo-dock.desktop")
                self.assertTrue(path.is_file())
                self.assertIn("Exec=pytpo-dock", path.read_text(encoding="utf-8"))
                self.assertTrue(manager.is_enabled())

                removed = manager.disable()

            self.assertTrue(removed)
            self.assertFalse(path.exists())

    def test_field_controller_tracks_pending_changes_against_real_state(self) -> None:
        manager = mock.Mock(spec=DockAutostartManager)
        manager.is_enabled.side_effect = [False, True]
        controller = DockAutostartFieldController(manager)

        self.assertFalse(controller.has_pending_changes(False))
        self.assertTrue(controller.has_pending_changes(True))

        errors = controller.apply_checked_state(True)

        self.assertEqual(errors, [])
        manager.enable.assert_called_once_with()
        self.assertFalse(controller.has_pending_changes(True))


if __name__ == "__main__":
    unittest.main()
