from __future__ import annotations

import unittest

from pytpo_appgrid.app import _parse_args


class AppGridCliTests(unittest.TestCase):
    def test_settings_flag_is_recognized(self) -> None:
        args = _parse_args(["pytpo-appgrid", "--settings"])
        self.assertTrue(args.settings)

    def test_settings_flag_defaults_to_false(self) -> None:
        args = _parse_args(["pytpo-appgrid"])
        self.assertFalse(args.settings)


if __name__ == "__main__":
    unittest.main()
