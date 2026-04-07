from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from barley_ide.desktop_integration import (
    APP_SPEC,
    DesktopEntrySpec,
    desktop_file_path,
    install_desktop_entry,
    installation_status,
    main,
    installed_icon_path,
    render_desktop_file,
    resolve_icon_source,
    uninstall_desktop_entry,
)


class DesktopEntriesTests(unittest.TestCase):
    def test_render_desktop_file_includes_barley_startup_wm_class(self) -> None:
        rendered = render_desktop_file()

        self.assertIn("Exec=barley-ide", rendered)
        self.assertIn("Icon=barley-ide", rendered)
        self.assertIn("StartupWMClass=barley-ide", rendered)

    def test_resolve_icon_source_prefers_package_icon(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            package_icon = tmp_path / "barley_ide" / "icon.png"
            package_icon.parent.mkdir(parents=True, exist_ok=True)
            package_icon.write_bytes(b"package-icon")

            spec = DesktopEntrySpec(
                desktop_id="barley-ide.desktop",
                command="barley-ide",
                display_name="Barley",
                comment="Standalone IDE",
                categories=("Development",),
                icon_candidates=("barley_ide/icon.png",),
            )

            with (
                mock.patch("barley_ide.desktop_integration.APP_SPEC", spec),
                mock.patch("barley_ide.desktop_integration.repo_root", return_value=tmp_path),
            ):
                self.assertEqual(resolve_icon_source(), package_icon)

    def test_install_desktop_entry_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            xdg_home = Path(tmpdir) / "xdg-data"
            commands: list[list[str]] = []

            with (
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg_home)}, clear=False),
                mock.patch(
                    "barley_ide.desktop_integration._run_command",
                    side_effect=lambda args, warnings: commands.append(args),
                ),
            ):
                warnings = install_desktop_entry()
                desktop_path = desktop_file_path()
                icon_path = installed_icon_path()

            self.assertEqual(warnings, [])
            self.assertEqual(desktop_path, xdg_home / "applications" / APP_SPEC.desktop_id)
            self.assertTrue(desktop_path.is_file())
            self.assertEqual(
                icon_path,
                xdg_home / "icons" / "hicolor" / "256x256" / "apps" / "barley-ide.png",
            )
            self.assertTrue(icon_path.is_file())
            self.assertIn("Exec=barley-ide", desktop_path.read_text(encoding="utf-8"))
            self.assertEqual(commands, [["update-desktop-database", str(xdg_home / "applications")]])

    def test_uninstall_desktop_entry_removes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            xdg_home = Path(tmpdir) / "xdg-data"

            with (
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg_home)}, clear=False),
                mock.patch("barley_ide.desktop_integration._run_command"),
            ):
                install_desktop_entry()
                desktop_installed, icon_installed = installation_status()
                self.assertTrue(desktop_installed)
                self.assertTrue(icon_installed)

                warnings = uninstall_desktop_entry()

            self.assertEqual(warnings, [])
            self.assertFalse((xdg_home / "applications" / APP_SPEC.desktop_id).exists())
            self.assertFalse((xdg_home / "icons" / "hicolor" / "256x256" / "apps" / "barley-ide.png").exists())

    def test_main_status_reports_installation_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            xdg_home = Path(tmpdir) / "xdg-data"

            with (
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg_home)}, clear=False),
                mock.patch("builtins.print") as print_mock,
            ):
                exit_code = main(["status"])

            self.assertEqual(exit_code, 0)
            print_mock.assert_called_with("barley-ide.desktop: desktop=no icon=no")


if __name__ == "__main__":
    unittest.main()
