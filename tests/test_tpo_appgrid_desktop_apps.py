from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pytpo_appgrid.desktop_apps import load_desktop_applications, parse_desktop_file


def _write_desktop_file(path: Path, body: str) -> None:
    path.write_text(body.strip() + "\n", encoding="utf-8")


class DesktopAppsTests(unittest.TestCase):
    def test_load_desktop_applications_filters_hidden_and_deduplicates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            primary_dir = root / "primary"
            secondary_dir = root / "secondary"
            primary_dir.mkdir()
            secondary_dir.mkdir()

            _write_desktop_file(
                primary_dir / "alpha.desktop",
                """
                [Desktop Entry]
                Type=Application
                Name=Alpha
                Exec=alpha
                Categories=Development;Utility;
                """,
            )
            _write_desktop_file(
                secondary_dir / "alpha.desktop",
                """
                [Desktop Entry]
                Type=Application
                Name=Alpha Override
                Exec=alpha-override
                Categories=Development;
                """,
            )
            _write_desktop_file(
                primary_dir / "hidden.desktop",
                """
                [Desktop Entry]
                Type=Application
                Name=Hidden
                Exec=hidden
                Hidden=true
                """,
            )
            _write_desktop_file(
                primary_dir / "beta.desktop",
                """
                [Desktop Entry]
                Type=Application
                Name=Beta
                Exec=beta
                Categories=Office;
                """,
            )

            apps = load_desktop_applications(paths=[primary_dir, secondary_dir])

            self.assertEqual([app.name for app in apps], ["Alpha", "Beta"])
            self.assertEqual(apps[0].categories, ("Development", "Utility"))

    def test_parse_desktop_file_reads_core_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            desktop_file = Path(tmpdir) / "sample.desktop"
            _write_desktop_file(
                desktop_file,
                """
                [Desktop Entry]
                Type=Application
                Name=Sample App
                Exec=sample --run
                Icon=sample-icon
                StartupWMClass=sample-app
                Categories=Graphics;Utility;
                Comment=Image tools
                """,
            )

            parsed = parse_desktop_file(desktop_file)

            self.assertEqual(parsed["Name"], "Sample App")
            self.assertEqual(parsed["Exec"], "sample --run")
            self.assertEqual(parsed["Icon"], "sample-icon")
            self.assertEqual(parsed["StartupWMClass"], "sample-app")
            self.assertEqual(parsed["Categories"], "Graphics;Utility;")


if __name__ == "__main__":
    unittest.main()
