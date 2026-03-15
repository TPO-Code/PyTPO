from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pytpo.desktop_entries import (
    DesktopAppSpec,
    app_alias_map,
    desktop_file_path,
    install_desktop_entries,
    main,
    installed_icon_path,
    render_desktop_file,
    resolve_icon_source,
    uninstall_desktop_entries,
)
from pytpo_dock.autostart import DockAutostartManager


class DesktopEntriesTests(unittest.TestCase):
    def test_render_desktop_file_uses_bare_command_and_icon_name(self) -> None:
        terminal = app_alias_map()["terminal"]

        rendered = render_desktop_file(terminal)

        self.assertIn("Exec=pytpo-terminal", rendered)
        self.assertIn("TryExec=pytpo-terminal", rendered)
        self.assertIn("Icon=pytpo-terminal", rendered)
        self.assertIn("Categories=System;TerminalEmulator;Utility;", rendered)

    def test_desktop_specs_use_app_specific_shared_icons(self) -> None:
        self.assertIn("icons/pytpo.png", app_alias_map()["pytpo"].icon_candidates)
        self.assertIn("icons/terminal.png", app_alias_map()["terminal"].icon_candidates)
        self.assertIn("icons/txt.png", app_alias_map()["text-editor"].icon_candidates)
        self.assertIn("icons/dock.png", app_alias_map()["dock"].icon_candidates)
        self.assertIn("icons/appgrid.png", app_alias_map()["appgrid"].icon_candidates)

    def test_resolve_icon_source_prefers_package_icon(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            package_icon = tmp_path / "pytpo_terminal" / "icon.png"
            package_icon.parent.mkdir(parents=True, exist_ok=True)
            package_icon.write_bytes(b"package-icon")

            spec = DesktopAppSpec(
                key="terminal",
                desktop_id="pytpo-terminal.desktop",
                command="pytpo-terminal",
                display_name="PyTPO Terminal",
                comment="Standalone terminal",
                categories=("Utility",),
                icon_candidates=("pytpo_terminal/icon.png", "icons/terminal.png"),
            )

            with mock.patch("pytpo.desktop_entries.repo_root", return_value=tmp_path):
                self.assertEqual(resolve_icon_source(spec), package_icon)

    def test_install_desktop_entries_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            xdg_home = Path(tmpdir) / "xdg-data"
            commands: list[list[str]] = []

            with (
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg_home)}, clear=False),
                mock.patch(
                    "pytpo.desktop_entries._run_command",
                    side_effect=lambda args, warnings: commands.append(args),
                ),
            ):
                warnings = install_desktop_entries(["terminal"])
                terminal = app_alias_map()["terminal"]
                desktop_path = desktop_file_path(terminal)
                icon_path = installed_icon_path(terminal)

            self.assertEqual(warnings, [])
            self.assertEqual(desktop_path, xdg_home / "applications" / "pytpo-terminal.desktop")
            self.assertTrue(desktop_path.is_file())
            self.assertEqual(
                icon_path,
                xdg_home / "icons" / "hicolor" / "256x256" / "apps" / "pytpo-terminal.png",
            )
            self.assertTrue(icon_path.is_file())
            self.assertIn("Exec=pytpo-terminal", desktop_path.read_text(encoding="utf-8"))
            self.assertEqual(commands, [["update-desktop-database", str(xdg_home / "applications")]])

    def test_uninstall_desktop_entries_removes_dock_autostart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            xdg_data_home = Path(tmpdir) / "xdg-data"
            xdg_config_home = Path(tmpdir) / "xdg-config"
            manager = DockAutostartManager()
            expected_path = xdg_config_home / "autostart" / "pytpo-dock.desktop"

            with (
                mock.patch.dict(
                    os.environ,
                    {"XDG_DATA_HOME": str(xdg_data_home), "XDG_CONFIG_HOME": str(xdg_config_home)},
                    clear=False,
                ),
                mock.patch("pytpo.desktop_entries._run_command"),
            ):
                manager.enable()
                self.assertTrue(expected_path.is_file())

                warnings = uninstall_desktop_entries(["dock"])

            self.assertEqual(warnings, [])
            self.assertFalse(expected_path.exists())

    def test_main_install_prompts_for_dock_autostart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            xdg_data_home = Path(tmpdir) / "xdg-data"
            xdg_config_home = Path(tmpdir) / "xdg-config"
            expected_path = xdg_config_home / "autostart" / "pytpo-dock.desktop"

            with (
                mock.patch.dict(
                    os.environ,
                    {"XDG_DATA_HOME": str(xdg_data_home), "XDG_CONFIG_HOME": str(xdg_config_home)},
                    clear=False,
                ),
                mock.patch("pytpo.desktop_entries._run_command"),
                mock.patch("sys.stdin.isatty", return_value=True),
                mock.patch("builtins.input", return_value="y"),
            ):
                exit_code = main(["install", "--app", "dock"])

            self.assertEqual(exit_code, 0)
            self.assertTrue(expected_path.is_file())


if __name__ == "__main__":
    unittest.main()
