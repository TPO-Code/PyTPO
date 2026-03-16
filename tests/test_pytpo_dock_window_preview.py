import unittest
from unittest import mock

from PySide6.QtCore import QAbstractAnimation, QPoint, QSize

from pytpo_dock.settings_dialog import DockVisualSettings
from pytpo_dock.ui import main_window
from pytpo_dock.x11_window_preview import X11WindowPreviewCapturer, _PixmapFormat


class _FakePixmap:
    def __init__(self, *, is_null: bool, width: int = 0, height: int = 0):
        self._is_null = is_null
        self._width = width
        self._height = height

    def isNull(self):
        return self._is_null

    def width(self):
        return self._width

    def height(self):
        return self._height


class _FakeScreenGeometry:
    def getRect(self):
        return (0, 0, 1920, 1080)


class _FakeScreen:
    def __init__(self, pixmap):
        self._pixmap = pixmap

    def grabWindow(self, native_id):
        self.native_id = native_id
        return self._pixmap

    def geometry(self):
        return _FakeScreenGeometry()


class _FakeReplyData:
    def __init__(self, raw: bytes):
        self.raw = raw


class _FakeImageReply:
    def __init__(self, *, depth: int, raw: bytes):
        self.depth = depth
        self.data = _FakeReplyData(raw)


class _FakeDockItem:
    def __init__(self, *, windows=None, win_id=None, name="App"):
        self.windows = windows or []
        self.win_id = win_id
        self.app_data = {"Name": name}
        self.active_states = []

    def set_active_window(self, active: bool):
        self.active_states.append(bool(active))


class _FakeLayoutItem:
    def __init__(self, widget):
        self._widget = widget

    def widget(self):
        return self._widget


class _FakeLayout:
    def __init__(self, widgets):
        self._items = [_FakeLayoutItem(widget) for widget in widgets]

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index]


class _FakeTimer:
    def __init__(self):
        self.started_with = []
        self.stop_calls = 0

    def start(self, value):
        self.started_with.append(value)

    def stop(self):
        self.stop_calls += 1


class _FakeAnimation:
    def __init__(self, state):
        self._state = state

    def state(self):
        return self._state


class _FakeDockPreviewItem:
    def __init__(self):
        self.app_data = {"Name": "Focused App"}
        self.windows = [{"id": "0x2a"}]

    def mapToGlobal(self, point):
        return QPoint(point.x(), point.y())

    def size(self):
        return QSize(20, 20)

    def isVisible(self):
        return True


class _FakeOffsetGeometry:
    def __init__(self, *, x: int, y: int, width: int, height: int):
        self._x = x
        self._y = y
        self._width = width
        self._height = height

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._width

    def height(self):
        return self._height

    def isNull(self):
        return False

    def getRect(self):
        return (self._x, self._y, self._width, self._height)


class DockWindowPreviewTests(unittest.TestCase):
    def test_capture_window_preview_prefers_xcffib_backend(self):
        backend_pixmap = _FakePixmap(is_null=False, width=400, height=240)

        class _Backend:
            backend_name = "xcffib-xcomposite"

            def capture(self, win_id):
                self.win_id = win_id
                return backend_pixmap

        dock = type("Dock", (), {"x11_preview_capturer": _Backend()})()
        with (
            mock.patch.object(main_window, "log_dock_debug"),
            mock.patch.object(main_window.QApplication, "screens", return_value=[]),
        ):
            result = main_window.CustomDock.capture_window_preview(dock, "0x44")

        self.assertIs(result, backend_pixmap)
        self.assertEqual(dock.x11_preview_capturer.win_id, "0x44")

    def test_capture_window_preview_falls_back_to_qscreen(self):
        fallback_pixmap = _FakePixmap(is_null=False, width=320, height=180)
        screen = _FakeScreen(fallback_pixmap)

        class _Backend:
            backend_name = "xcffib-xcomposite"

            def capture(self, _win_id):
                return _FakePixmap(is_null=True)

        dock = type("Dock", (), {"x11_preview_capturer": _Backend()})()
        with (
            mock.patch.object(main_window, "log_dock_debug"),
            mock.patch.object(main_window.QApplication, "screens", return_value=[screen]),
        ):
            result = main_window.CustomDock.capture_window_preview(dock, "0x2a")

        self.assertIs(result, fallback_pixmap)
        self.assertEqual(screen.native_id, 42)

    def test_refresh_active_window_highlight_marks_matching_dock_item(self):
        active_item = _FakeDockItem(windows=[{"id": "0x2a"}], name="Focused App")
        inactive_item = _FakeDockItem(windows=[{"id": "0x3"}], name="Other App")

        class _DummyDock:
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _dock_item_matches_active_window = main_window.CustomDock._dock_item_matches_active_window
            refresh_active_window_highlight = main_window.CustomDock.refresh_active_window_highlight

            def __init__(self):
                self._last_active_window_id = ""
                self.app_row_layout = _FakeLayout([active_item, inactive_item])

            def _active_window_id(self):
                return "2a"

        dock = _DummyDock()
        with mock.patch.object(main_window, "log_dock_debug"):
            dock.refresh_active_window_highlight(force=True)

        self.assertEqual(active_item.active_states, [True])
        self.assertEqual(inactive_item.active_states, [False])

    def test_hidden_and_visible_y_respect_screen_offset_and_overshoot(self):
        geometry = _FakeOffsetGeometry(x=50, y=100, width=1920, height=1080)

        class _DummyDock:
            _visible_y = main_window.CustomDock._visible_y
            _hidden_y = main_window.CustomDock._hidden_y

            def height(self):
                return 80

        dock = _DummyDock()
        self.assertEqual(dock._visible_y(geometry), 1085)
        self.assertEqual(dock._hidden_y(geometry), 1200)

    def test_schedule_preview_defers_while_visibility_animation_is_running(self):
        item = _FakeDockPreviewItem()

        class _DummyDock:
            _preview_delay_ms = main_window.CustomDock._preview_delay_ms
            _is_visibility_animation_running = main_window.CustomDock._is_visibility_animation_running
            schedule_preview = main_window.CustomDock.schedule_preview

            def __init__(self):
                self.anim = _FakeAnimation(QAbstractAnimation.Running)
                self.pending_preview_item = None
                self.active_preview_item = None
                self.active_preview_anchor_rect = None
                self.preview_hide_timer = _FakeTimer()
                self.preview_timer = _FakeTimer()

            def set_settings_revealed(self, _revealed):
                return None

            def _log_window_state(self, *_args, **_kwargs):
                return None

        dock = _DummyDock()
        with mock.patch.object(main_window, "isValid", return_value=True):
            dock.schedule_preview(item)

        self.assertIs(dock.pending_preview_item, item)
        self.assertEqual(dock.preview_timer.started_with, [])

    def test_restart_deferred_preview_uses_short_hover_delay(self):
        item = _FakeDockPreviewItem()

        class _DummyDock:
            _preview_delay_ms = main_window.CustomDock._preview_delay_ms
            _cursor_over_widget = main_window.CustomDock._cursor_over_widget
            _restart_deferred_preview_if_hovered = main_window.CustomDock._restart_deferred_preview_if_hovered

            def __init__(self):
                self.pending_preview_item = item
                self.preview_timer = _FakeTimer()

        dock = _DummyDock()
        with (
            mock.patch.object(main_window, "isValid", return_value=True),
            mock.patch.object(main_window.QCursor, "pos", return_value=QPoint(5, 5)),
        ):
            dock._restart_deferred_preview_if_hovered()

        self.assertEqual(dock.preview_timer.started_with, [300])

    def test_ensure_hidden_window_mapped_keeps_window_shown_offscreen(self):
        geometry = _FakeOffsetGeometry(x=50, y=100, width=1920, height=1080)

        class _DummyDock:
            ensure_hidden_window_mapped = main_window.CustomDock.ensure_hidden_window_mapped
            _hidden_y = main_window.CustomDock._hidden_y
            _visibility_animation_mode = main_window.CustomDock._visibility_animation_mode

            def __init__(self):
                self.visible = False
                self.position = None
                self.opacity = None
                self.dock_settings = type("Settings", (), {"visibility_animation_mode": "fade"})()

            def _screen_geometry(self):
                return geometry

            def width(self):
                return 200

            def height(self):
                return 80

            def move(self, *args):
                if len(args) == 1:
                    point = args[0]
                    self.position = (point.x(), point.y())
                else:
                    self.position = (args[0], args[1])

            def setWindowOpacity(self, value):
                self.opacity = value

            def isVisible(self):
                return self.visible

            def show(self):
                self.visible = True

            def _log_window_state(self, *_args, **_kwargs):
                return None

        dock = _DummyDock()
        dock.ensure_hidden_window_mapped()

        self.assertTrue(dock.visible)
        self.assertEqual(dock.opacity, 0.0)
        self.assertEqual(dock.position, (910, 1200))

    def test_dock_visual_settings_include_hover_and_focus_highlight_fields(self):
        settings = DockVisualSettings.from_mapping(
            {
                "visibility_animation_mode": "slide",
                "hover_highlight_color": "#123456",
                "hover_highlight_opacity": 41,
                "hover_highlight_radius": 9,
                "focused_window_highlight_color": "#abcdef",
                "focused_window_highlight_opacity": 62,
                "focused_window_highlight_radius": 15,
            }
        )

        self.assertEqual(settings.visibility_animation_mode, "slide")
        self.assertEqual(settings.hover_highlight_color, "#123456")
        self.assertEqual(settings.hover_highlight_opacity, 41)
        self.assertEqual(settings.hover_highlight_radius, 9)
        self.assertEqual(settings.focused_window_highlight_color, "#abcdef")
        self.assertEqual(settings.focused_window_highlight_opacity, 62)
        self.assertEqual(settings.focused_window_highlight_radius, 15)
        self.assertIn("visibility_animation_mode", settings.to_mapping())
        self.assertIn("hover_highlight_color", settings.to_mapping())
        self.assertIn("focused_window_highlight_color", settings.to_mapping())

    def test_qimage_from_32bpp_reply_preserves_argb(self):
        capturer = X11WindowPreviewCapturer()
        capturer._image_byte_order = capturer._LSB_FIRST
        capturer._format_by_depth = {32: _PixmapFormat(bits_per_pixel=32, scanline_pad=32)}

        image = capturer._qimage_from_reply(
            _FakeImageReply(depth=32, raw=bytes([0x10, 0x20, 0x30, 0x80])),
            width=1,
            height=1,
        )

        self.assertIsNotNone(image)
        color = image.pixelColor(0, 0)
        self.assertEqual((color.red(), color.green(), color.blue(), color.alpha()), (0x30, 0x20, 0x10, 0x80))

    def test_qimage_from_24bpp_reply_uses_bgr_bytes(self):
        capturer = X11WindowPreviewCapturer()
        capturer._image_byte_order = capturer._LSB_FIRST
        capturer._format_by_depth = {24: _PixmapFormat(bits_per_pixel=24, scanline_pad=32)}

        image = capturer._qimage_from_reply(
            _FakeImageReply(depth=24, raw=bytes([0x10, 0x20, 0x30, 0x00])),
            width=1,
            height=1,
        )

        self.assertIsNotNone(image)
        color = image.pixelColor(0, 0)
        self.assertEqual((color.red(), color.green(), color.blue()), (0x30, 0x20, 0x10))


if __name__ == "__main__":
    unittest.main()
