import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from pytpo_dock import match_diagnostics
from pytpo_dock.ui import main_window
from pytpo_dock import window_matching


class WindowMatchingTests(unittest.TestCase):
    def test_parse_wmctrl_windows_uses_wm_class_not_host(self):
        output = "\n".join(
            [
                "0x01200007 0 4321 ace Mozilla Firefox",
                "0x01200008 -1 4321 ace Hidden Firefox",
            ]
        )

        with (
            mock.patch.object(
                window_matching,
                "_read_x_window_identity",
                return_value={
                    "wm_class": "navigator.firefox",
                    "instance": "navigator",
                    "class": "firefox",
                },
            ),
            mock.patch.object(
                window_matching,
                "_process_identity",
                return_value={
                    "process_name": "firefox",
                    "executable_name": "firefox",
                    "script_name": "",
                },
            ),
        ):
            windows = window_matching.parse_wmctrl_windows(output)

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["host"], "ace")
        self.assertEqual(windows[0]["instance"], "navigator")
        self.assertEqual(windows[0]["class"], "firefox")
        self.assertEqual(windows[0]["icon"], "firefox")
        self.assertEqual(windows[0]["app_name"], "Firefox")

    def test_python_launcher_matches_window_by_script_identity(self):
        window = {
            "id": "0x01200009",
            "wm_class": "python3.python3",
            "instance": "python3",
            "class": "python3",
            "title": "My Tool",
            "process_name": "python3",
            "executable_name": "python3",
            "script_name": "my_tool",
        }
        app_data = {
            "path": "/apps/my-tool.desktop",
            "desktop_id": "my-tool.desktop",
            "Name": "My Tool",
            "GenericName": "",
            "Exec": "python3 /opt/my_tool.py",
            "Icon": "my-tool",
            "StartupWMClass": "",
        }

        score = window_matching.score_window_match(window, app_data)
        self.assertGreaterEqual(score, window_matching.match_threshold())

    def test_generic_terminal_tokens_do_not_claim_unrelated_app(self):
        window = {
            "id": "0x01200010",
            "wm_class": "gnome-system-monitor.gnome-system-monitor",
            "instance": "gnome-system-monitor",
            "class": "gnome-system-monitor",
            "title": "System Monitor",
            "process_name": "gnome-system-monitor",
            "executable_name": "gnome-system-monitor",
            "script_name": "",
        }
        app_data = {
            "path": "/apps/pytpo-terminal.desktop",
            "desktop_id": "pytpo-terminal.desktop",
            "Name": "PyTPO Terminal",
            "GenericName": "Terminal",
            "Exec": "pytpo-terminal",
            "Icon": "pytpo-terminal",
            "StartupWMClass": "",
        }

        score = window_matching.score_window_match(window, app_data)
        self.assertLess(score, window_matching.match_threshold())

    def test_generic_script_names_resolve_to_project_identity(self):
        self.assertEqual(
            window_matching._path_identity("/home/aceofjohn/Work/Repos/PyTPO/pytpo/app.py"),
            "pytpo",
        )
        self.assertEqual(
            window_matching._path_identity("/home/aceofjohn/Work/Repos/BeerGarden/client/main.py"),
            "beergarden",
        )

    def test_specific_pytpo_subapp_outranks_base_launcher(self):
        window = {
            "id": "0x04400008",
            "wm_class": "pytpo-text-editor.pytpo text editor",
            "instance": "pytpo-text-editor",
            "class": "pytpo text editor",
            "title": "Text Editor pop-os Untitled - PyTPO Text Editor",
            "process_name": "pytpo-text-edit",
            "executable_name": "python3.11",
            "script_name": "pytpo-text-editor",
        }
        base_app = {
            "path": "/home/aceofjohn/.local/share/applications/pytpo.desktop",
            "desktop_id": "pytpo.desktop",
            "Name": "PyTPO",
            "GenericName": "",
            "Exec": "pytpo",
            "Icon": "pytpo",
            "StartupWMClass": "",
        }
        editor_app = {
            "path": "/home/aceofjohn/.local/share/applications/pytpo-text-editor.desktop",
            "desktop_id": "pytpo-text-editor.desktop",
            "Name": "PyTPO Text Editor",
            "GenericName": "",
            "Exec": "pytpo-text-editor %F",
            "Icon": "pytpo-text-editor",
            "StartupWMClass": "",
        }

        self.assertGreater(
            window_matching.score_window_match(window, editor_app),
            window_matching.score_window_match(window, base_app),
        )

    def test_update_dock_items_groups_runtime_windows_and_counts_instances(self):
        firefox_app = {
            "path": "/apps/firefox.desktop",
            "desktop_id": "firefox.desktop",
            "Name": "Firefox",
            "Exec": "firefox",
            "Icon": "firefox",
            "StartupWMClass": "firefox",
            "GenericName": "",
        }

        running_windows = [
            {
                "id": "0x1",
                "wm_class": "navigator.firefox",
                "instance": "navigator",
                "class": "firefox",
                "title": "Mozilla Firefox",
                "process_name": "firefox",
                "executable_name": "firefox",
                "script_name": "",
                "runtime_id": "firefox",
                "app_name": "Firefox",
                "icon": "firefox",
            },
            {
                "id": "0x2",
                "wm_class": "navigator.firefox",
                "instance": "navigator",
                "class": "firefox",
                "title": "Docs - Mozilla Firefox",
                "process_name": "firefox",
                "executable_name": "firefox",
                "script_name": "",
                "runtime_id": "firefox",
                "app_name": "Firefox",
                "icon": "firefox",
            },
            {
                "id": "0x3",
                "wm_class": "gimp.gimp-2-10",
                "instance": "gimp",
                "class": "gimp-2-10",
                "title": "image.xcf - GIMP",
                "process_name": "gimp-2.10",
                "executable_name": "gimp-2.10",
                "script_name": "",
                "runtime_id": "gimp210",
                "app_name": "Gimp 2 10",
                "icon": "gimp",
            },
            {
                "id": "0x4",
                "wm_class": "gimp.gimp-2-10",
                "instance": "gimp",
                "class": "gimp-2-10",
                "title": "second.xcf - GIMP",
                "process_name": "gimp-2.10",
                "executable_name": "gimp-2.10",
                "script_name": "",
                "runtime_id": "gimp210",
                "app_name": "Gimp 2 10",
                "icon": "gimp",
            },
        ]

        class _DummyDock:
            _assign_windows_to_apps = main_window.CustomDock._assign_windows_to_apps
            _write_window_snapshot = main_window.CustomDock._write_window_snapshot
            _runtime_window_groups = main_window.CustomDock._runtime_window_groups
            update_dock_items = main_window.CustomDock.update_dock_items

            def __init__(self):
                self.pinned_apps = ["/apps/firefox.desktop"]
                self.last_dock_state = []
                self.registry = {"firefox": firefox_app}
                self.rebuilt_items = None

            def get_running_windows(self):
                return list(running_windows)

            def _known_apps_by_path(self):
                return {"/apps/firefox.desktop": firefox_app}

            def rebuild_layout(self, items):
                self.rebuilt_items = items

        dock = _DummyDock()
        with mock.patch.object(main_window, "log_dock_debug"):
            dock.update_dock_items()

        self.assertIsNotNone(dock.rebuilt_items)
        self.assertEqual(len(dock.rebuilt_items), 2)

        firefox_item = dock.rebuilt_items[0]
        self.assertEqual(firefox_item["path"], "/apps/firefox.desktop")
        self.assertTrue(firefox_item["is_running"])
        self.assertEqual(len(firefox_item["windows"]), 2)

        runtime_item = dock.rebuilt_items[1]
        self.assertEqual(runtime_item["path"], "runtime://gimp210")
        self.assertEqual(runtime_item["data"]["Name"], "Gimp 2 10")
        self.assertEqual(runtime_item["data"]["Icon"], "gimp")
        self.assertEqual(len(runtime_item["windows"]), 2)

    def test_write_window_snapshot_outputs_json_and_markdown(self):
        running_windows = [
            {
                "id": "0x1",
                "title": "Mozilla Firefox",
                "wm_class": "navigator.firefox",
                "instance": "navigator",
                "class": "firefox",
                "pid": 100,
                "process_name": "firefox",
                "executable_name": "firefox",
                "script_name": "",
                "app_name": "Firefox",
                "icon": "firefox",
                "runtime_id": "firefox",
            }
        ]
        known_apps_by_path = {
            "/apps/firefox.desktop": {
                "path": "/apps/firefox.desktop",
                "desktop_id": "firefox.desktop",
                "Name": "Firefox",
                "Exec": "firefox",
                "Icon": "firefox",
                "StartupWMClass": "firefox",
            }
        }
        assigned_windows = {"/apps/firefox.desktop": list(running_windows)}
        target_items = [
            {
                "path": "/apps/firefox.desktop",
                "data": known_apps_by_path["/apps/firefox.desktop"],
                "is_pinned": False,
                "is_running": True,
                "windows": list(running_windows),
            }
        ]

        with TemporaryDirectory() as tmp_dir:
            json_path = Path(tmp_dir) / "snapshot.json"
            md_path = Path(tmp_dir) / "snapshot.md"
            with (
                mock.patch.object(match_diagnostics, "dock_window_snapshot_json_path", return_value=json_path),
                mock.patch.object(match_diagnostics, "dock_window_snapshot_markdown_path", return_value=md_path),
            ):
                written_json, written_md = match_diagnostics.write_window_snapshot(
                    running_windows=running_windows,
                    known_apps_by_path=known_apps_by_path,
                    assigned_windows=assigned_windows,
                    unmatched_windows=[],
                    target_items=target_items,
                )
            json_text = json_path.read_text(encoding="utf-8")
            md_text = md_path.read_text(encoding="utf-8")

        self.assertEqual(written_json, str(json_path))
        self.assertEqual(written_md, str(md_path))
        self.assertIn("Firefox", json_text)
        self.assertIn("Dock Window Snapshot", md_text)

    def test_get_running_windows_uses_xlib_backend_records(self):
        class _DummyDock:
            def _is_own_window(self, _win_id):
                return False

        with (
            mock.patch.object(
                main_window,
                "list_windows_via_xlib",
                return_value=[
                    {
                        "id": "0x1",
                        "desktop": "0",
                        "pid": 100,
                        "host": "",
                        "title": "Mozilla Firefox",
                        "wm_class": "navigator.firefox",
                        "instance": "navigator",
                        "class": "firefox",
                    }
                ],
            ),
            mock.patch.object(
                window_matching,
                "_process_identity",
                return_value={
                    "process_name": "firefox",
                    "executable_name": "firefox",
                    "script_name": "",
                },
            ),
        ):
            windows = main_window.CustomDock.get_running_windows(_DummyDock())

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["class"], "firefox")


if __name__ == "__main__":
    unittest.main()
