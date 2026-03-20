import unittest
from unittest import mock

from pytpo_dock import app as dock_app
from pytpo_dock.ui import main_window


class _FakeApp:
    def __init__(self, argv):
        self.argv = argv
        self.quit_on_last_window_closed = None
        self.window_icon = None

    def setQuitOnLastWindowClosed(self, enabled):
        self.quit_on_last_window_closed = enabled

    def setWindowIcon(self, icon):
        self.window_icon = icon

    def exec(self):
        return 123


class _FakeDock:
    def __init__(self):
        self._geometry = (0, 0, 100, 40)
        self._width = 100
        self._height = 40
        self.moves = []
        self.window_icon = None

    def geometry(self):
        return self

    def getRect(self):
        return self._geometry

    def width(self):
        return self._width

    def height(self):
        return self._height

    def isVisible(self):
        return False

    def move(self, x, y):
        self.moves.append((x, y))

    def setWindowIcon(self, icon):
        self.window_icon = icon


class _FakeScreenGeometry:
    def x(self):
        return 0

    def y(self):
        return 0

    def width(self):
        return 1920

    def height(self):
        return 1080

    def getRect(self):
        return (0, 0, 1920, 1080)


class _FakeScreen:
    def geometry(self):
        return _FakeScreenGeometry()


class DockAppTests(unittest.TestCase):
    def test_main_disables_quit_on_last_window_closed(self):
        created = {}

        def fake_qapplication(argv):
            app = _FakeApp(argv)
            created["app"] = app
            return app

        class _FakeIcon:
            current_theme = ""

            def __init__(self, path=""):
                self.path = path

            @classmethod
            def themeName(cls):
                return cls.current_theme

            @classmethod
            def setThemeName(cls, name):
                cls.current_theme = name

            def isNull(self):
                return not bool(self.path)

        fake_qapplication.primaryScreen = staticmethod(lambda: _FakeScreen())

        with (
            mock.patch.object(dock_app, "QApplication", fake_qapplication),
            mock.patch.object(dock_app, "CustomDock", _FakeDock),
            mock.patch.object(dock_app, "reset_dock_debug_log", return_value="/tmp/dock.log"),
            mock.patch.object(dock_app, "install_qt_debug_message_logger"),
            mock.patch.object(dock_app, "log_dock_debug"),
            mock.patch.object(dock_app, "ensure_xlib_available"),
            mock.patch.object(dock_app, "QIcon", _FakeIcon),
        ):
            exit_code = dock_app.main(["pytpo-dock"])

        self.assertEqual(exit_code, 123)
        self.assertIs(created["app"].quit_on_last_window_closed, False)

    def test_open_settings_dialog_uses_dock_as_parent(self):
        captured = {}

        class _FakeDialog:
            def __init__(self, *, on_applied=None, parent=None):
                captured["on_applied"] = on_applied
                captured["parent"] = parent

            def exec(self):
                captured["exec_called"] = True
                return 0

        class _DummyDock:
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.hide_preview_called = False

            def hide_preview(self):
                self.hide_preview_called = True

            def apply_dock_settings(self):
                return None

        dock = _DummyDock()
        with mock.patch.object(main_window, "DockSettingsDialog", _FakeDialog):
            main_window.CustomDock.open_settings_dialog(dock)

        self.assertTrue(dock.hide_preview_called)
        self.assertIs(captured["parent"], dock)
        self.assertIs(captured["on_applied"].__self__, dock)
        self.assertIs(captured["on_applied"].__func__, _DummyDock.apply_dock_settings)
        self.assertTrue(captured["exec_called"])

    def test_main_returns_error_when_xlib_backend_is_missing(self):
        with (
            mock.patch.object(dock_app, "reset_dock_debug_log", return_value="/tmp/dock.log"),
            mock.patch.object(dock_app, "install_qt_debug_message_logger"),
            mock.patch.object(dock_app, "log_dock_debug"),
            mock.patch.object(dock_app, "ensure_xlib_available", side_effect=RuntimeError("xlib missing")),
        ):
            exit_code = dock_app.main(["pytpo-dock"])

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
