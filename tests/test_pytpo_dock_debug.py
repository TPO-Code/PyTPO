import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from pytpo_dock import debug


class DockDebugTests(unittest.TestCase):
    def test_log_dock_debug_is_disabled_by_default(self):
        with TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "dock.log"
            with (
                mock.patch.object(debug, "dock_debug_log_path", return_value=log_path),
                mock.patch.dict(os.environ, {"PYTPO_DOCK_DEBUG": ""}, clear=False),
            ):
                debug.log_dock_debug("dock-event", answer=42)

            self.assertFalse(log_path.exists())

    def test_log_dock_debug_writes_when_debug_enabled(self):
        with TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "dock.log"
            with (
                mock.patch.object(debug, "dock_debug_log_path", return_value=log_path),
                mock.patch.dict(os.environ, {"PYTPO_DOCK_DEBUG": "1"}, clear=False),
            ):
                debug.log_dock_debug("dock-event", answer=42)

            self.assertTrue(log_path.exists())
            self.assertIn("dock-event", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
