import unittest
from unittest import mock

from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QRect, QSize, Qt

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
    def __init__(self, *, windows=None, win_id=None, name="App", path="/tmp/app.desktop", is_running=True):
        self.windows = windows or []
        self.win_id = win_id
        self.app_data = {"Name": name, "path": path}
        self.is_running = is_running
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
    def __init__(self, *, is_running=True, path="/tmp/focused.desktop"):
        self.app_data = {"Name": "Focused App", "path": path}
        self.windows = [{"id": "0x2a"}]
        self.is_running = is_running

    def mapToGlobal(self, point):
        return QPoint(point.x(), point.y())

    def size(self):
        return QSize(20, 20)

    def width(self):
        return 20

    def height(self):
        return 20

    def isVisible(self):
        return True


class _FakePreviewPopup:
    def __init__(self, *, visible: bool):
        self._visible = visible

    def isVisible(self):
        return self._visible

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def close(self):
        self._visible = False

    def setVisible(self, value: bool):
        self._visible = bool(value)


class _FakeScreenBounds:
    def __init__(self, *, left=0, top=0, width=1920, height=1080):
        self._rect = QRect(left, top, width, height)

    def isNull(self):
        return False

    def contains(self, point):
        return self._rect.contains(point)

    def bottom(self):
        return self._rect.bottom()


class _FakePopupSizer:
    def __init__(self, width=280, height=180):
        self._size = QSize(width, height)
        self.moves = []
        self.updated_with = []

    def isVisible(self):
        return True

    def update_content(self, previews, *, animate_changes=True):
        self.updated_with.append([dict(preview) for preview in previews])
        self.animate_changes = animate_changes

    def sizeHint(self):
        return self._size

    def resize(self, size):
        self._size = QSize(size)

    def width(self):
        return self._size.width()

    def height(self):
        return self._size.height()

    def move(self, x, y):
        self.moves.append((x, y))

    def show(self):
        return None


class _FakePreviewHost:
    def __init__(self):
        self.fixed_sizes = []
        self._size = QSize(420, 220)

    def setFixedSize(self, width, height):
        self.fixed_sizes.append((width, height))
        self._size = QSize(width, height)

    def mapFromGlobal(self, point):
        return QPoint(point.x(), point.y())

    def width(self):
        return self._size.width()

    def height(self):
        return self._size.height()


class _FakeContainer:
    def __init__(self, width=360):
        self._width = width

    def sizeHint(self):
        return QSize(self._width, 80)


class _FakeOpacityEffect:
    def __init__(self):
        self.values = []

    def setOpacity(self, value):
        self.values.append(value)


class _FakeFadeAnimation:
    def __init__(self):
        self.stop_calls = 0
        self.start_calls = 0
        self.start_values = []
        self.end_values = []

    def stop(self):
        self.stop_calls += 1

    def setStartValue(self, value):
        self.start_values.append(value)

    def setEndValue(self, value):
        self.end_values.append(value)

    def start(self):
        self.start_calls += 1


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
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _dock_item_matches_active_window = main_window.CustomDock._dock_item_matches_active_window
            refresh_active_window_highlight = main_window.CustomDock.refresh_active_window_highlight

            def __init__(self):
                self._last_active_window_id = ""
                self.app_row_layout = _FakeLayout([active_item, inactive_item])
                self.active_preview_app_path = ""
                self.current_preview_entries = []
                self.pending_preview_item = None

            def _active_window_id(self):
                return "2a"

        dock = _DummyDock()
        with mock.patch.object(main_window, "log_dock_debug"):
            dock.refresh_active_window_highlight(force=True)

        self.assertEqual(active_item.active_states, [True])
        self.assertEqual(inactive_item.active_states, [False])

    def test_refresh_active_window_highlight_hides_dock_when_preview_is_open_and_focus_moves_to_app(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            _dock_item_matches_active_window = main_window.CustomDock._dock_item_matches_active_window
            refresh_active_window_highlight = main_window.CustomDock.refresh_active_window_highlight
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self._last_active_window_id = ""
                self.app_row_layout = _FakeLayout([])
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2a", "title": "Focused App"}]
                self.pending_preview_item = None
                self.preview_item_activation_guard_deadline = 0.0
                self.hide_dock_calls = 0

            def _active_window_id(self):
                return "2a"

            def _is_own_window(self, _win_id):
                return False

            def _find_dock_item_by_app_path(self, _app_path):
                return None

            def hide_dock(self):
                self.hide_dock_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "log_dock_debug"):
            dock.refresh_active_window_highlight(force=True)

        self.assertEqual(dock.hide_dock_calls, 1)

    def test_refresh_active_window_highlight_keeps_dock_open_while_preview_close_guard_is_active(self):
        preview_item = _FakeDockItem(
            windows=[{"id": "0x2a"}, {"id": "0x2b"}],
            name="Focused App",
            path="/tmp/focused.desktop",
        )

        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            _dock_item_matches_active_window = main_window.CustomDock._dock_item_matches_active_window
            refresh_active_window_highlight = main_window.CustomDock.refresh_active_window_highlight
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self._last_active_window_id = ""
                self.app_row_layout = _FakeLayout([preview_item])
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2a", "title": "Focused App"}]
                self.pending_preview_item = None
                self.preview_item_activation_guard_deadline = float("inf")
                self.hide_dock_calls = 0

            def _active_window_id(self):
                return "2b"

            def _is_own_window(self, _win_id):
                return False

            def _find_dock_item_by_app_path(self, app_path):
                if app_path == "/tmp/focused.desktop":
                    return preview_item
                return None

            def hide_dock(self):
                self.hide_dock_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "log_dock_debug"):
            dock.refresh_active_window_highlight(force=True)

        self.assertEqual(dock.hide_dock_calls, 0)

    def test_refresh_active_window_highlight_keeps_dock_open_while_preview_guard_is_active_even_if_item_windows_are_stale(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            _dock_item_matches_active_window = main_window.CustomDock._dock_item_matches_active_window
            refresh_active_window_highlight = main_window.CustomDock.refresh_active_window_highlight
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self._last_active_window_id = ""
                self.app_row_layout = _FakeLayout([])
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Focused App"}]
                self.pending_preview_item = None
                self.preview_item_activation_guard_deadline = float("inf")
                self.hide_dock_calls = 0

            def _active_window_id(self):
                return "5e00008"

            def _is_own_window(self, _win_id):
                return False

            def _find_dock_item_by_app_path(self, _app_path):
                return None

            def hide_dock(self):
                self.hide_dock_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "log_dock_debug"):
            dock.refresh_active_window_highlight(force=True)

        self.assertEqual(dock.hide_dock_calls, 0)

    def test_handle_preview_interaction_started_arms_guards_on_mouse_press(self):
        class _DummyDock:
            _arm_preview_visibility_guard = main_window.CustomDock._arm_preview_visibility_guard
            _arm_preview_item_activation_guard = main_window.CustomDock._arm_preview_item_activation_guard
            handle_preview_interaction_started = main_window.CustomDock.handle_preview_interaction_started

            def __init__(self):
                self.preview_visibility_guard_deadline = 0.0
                self.preview_item_activation_guard_deadline = 0.0
                self._last_mouse_buttons = Qt.MouseButton.NoButton

        dock = _DummyDock()
        with mock.patch.object(main_window.time, "monotonic", return_value=10.0):
            dock.handle_preview_interaction_started()

        self.assertEqual(dock._last_mouse_buttons, Qt.MouseButton.LeftButton)
        self.assertEqual(dock.preview_visibility_guard_deadline, 10.5)
        self.assertEqual(dock.preview_item_activation_guard_deadline, 10.5)

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

    def test_handle_item_activation_opens_preview_for_running_item(self):
        item = _FakeDockPreviewItem()

        class _DummyDock:
            _dock_item_app_path = main_window.CustomDock._dock_item_app_path
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            handle_item_activation = main_window.CustomDock.handle_item_activation
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=False)
                self.active_preview_app_path = ""
                self.preview_item_activation_guard_deadline = 0.0
                self.previewed_items = []
                self.window_actions = []
                self.hide_preview_calls = 0

            def show_preview_for_item(self, dock_item):
                self.previewed_items.append(dock_item)
                return True

            def execute_window_action(self, action_name, payload):
                self.window_actions.append((action_name, payload))

            def _item_action_payload(self, dock_item):
                return {"app_data": dock_item.app_data}

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "isValid", return_value=True):
            dock.handle_item_activation(item)

        self.assertEqual(dock.previewed_items, [item])
        self.assertEqual(dock.window_actions, [])
        self.assertEqual(dock.hide_preview_calls, 0)

    def test_handle_item_activation_hides_preview_when_same_app_icon_is_clicked_again(self):
        item = _FakeDockPreviewItem()

        class _DummyDock:
            _dock_item_app_path = main_window.CustomDock._dock_item_app_path
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            handle_item_activation = main_window.CustomDock.handle_item_activation
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = item.app_data["path"]
                self.preview_item_activation_guard_deadline = 0.0
                self.hide_preview_calls = 0
                self.previewed_items = []

            def show_preview_for_item(self, dock_item):
                self.previewed_items.append(dock_item)
                return True

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "isValid", return_value=True):
            dock.handle_item_activation(item)

        self.assertEqual(dock.hide_preview_calls, 1)
        self.assertEqual(dock.previewed_items, [])

    def test_handle_item_activation_ignores_clicks_while_preview_action_guard_is_active(self):
        item = _FakeDockPreviewItem()

        class _DummyDock:
            _dock_item_app_path = main_window.CustomDock._dock_item_app_path
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            handle_item_activation = main_window.CustomDock.handle_item_activation
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = item.app_data["path"]
                self.preview_item_activation_guard_deadline = 15.0
                self.hide_preview_calls = 0
                self.previewed_items = []

            def show_preview_for_item(self, dock_item):
                self.previewed_items.append(dock_item)
                return True

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        with (
            mock.patch.object(main_window, "isValid", return_value=True),
            mock.patch.object(main_window.time, "monotonic", return_value=10.0),
        ):
            dock.handle_item_activation(item)

        self.assertEqual(dock.hide_preview_calls, 0)
        self.assertEqual(dock.previewed_items, [])

    def test_handle_item_activation_launches_non_running_item(self):
        item = _FakeDockPreviewItem(is_running=False)

        class _DummyDock:
            _dock_item_app_path = main_window.CustomDock._dock_item_app_path
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            handle_item_activation = main_window.CustomDock.handle_item_activation
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/other.desktop"
                self.preview_item_activation_guard_deadline = 0.0
                self.window_actions = []
                self.hide_preview_calls = 0

            def show_preview_for_item(self, dock_item):
                raise AssertionError("show_preview_for_item should not be called for non-running items")

            def execute_window_action(self, action_name, payload):
                self.window_actions.append((action_name, payload))

            def _item_action_payload(self, dock_item):
                return {"app_data": dock_item.app_data}

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "isValid", return_value=True):
            dock.handle_item_activation(item)

        self.assertEqual(dock.window_actions, [("new_window", {"app_data": item.app_data})])
        self.assertEqual(dock.hide_preview_calls, 1)

    def test_refresh_preview_after_action_updates_layout_and_visible_preview(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            refresh_preview_after_action = main_window.CustomDock.refresh_preview_after_action

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = []
                self.pending_preview_item = None
                self.calls = []

            def update_dock_items(self):
                self.calls.append("update")

            def refresh_active_preview(self):
                self.calls.append("refresh")

        dock = _DummyDock()
        dock.refresh_preview_after_action()

        self.assertEqual(dock.calls, ["update", "refresh"])

    def test_refresh_preview_after_action_restores_hidden_popup_when_preview_state_is_active(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            refresh_preview_after_action = main_window.CustomDock.refresh_preview_after_action

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=False)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Two"}]
                self.pending_preview_item = None
                self.calls = []

            def update_dock_items(self):
                self.calls.append("update")

            def refresh_active_preview(self):
                self.calls.append("refresh")

        dock = _DummyDock()
        dock.refresh_preview_after_action()

        self.assertEqual(dock.calls, ["update", "refresh"])

    def test_restore_preview_visibility_if_guarded_reopens_hidden_panel(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _preview_visibility_guard_active = main_window.CustomDock._preview_visibility_guard_active
            _restore_preview_visibility_if_guarded = main_window.CustomDock._restore_preview_visibility_if_guarded

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=False)
                self.preview_popup_opacity = _FakeOpacityEffect()
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Two"}]
                self.pending_preview_item = None
                self.preview_visibility_guard_deadline = 15.0
                self.visible = True
                self.calls = []

            def isVisible(self):
                return self.visible

            def show(self):
                self.visible = True
                self.calls.append("show")

            def setWindowOpacity(self, value):
                self.calls.append(("opacity", value))

            def refresh_active_preview(self):
                self.calls.append("refresh")

        dock = _DummyDock()
        with mock.patch.object(main_window.time, "monotonic", return_value=10.0):
            restored = dock._restore_preview_visibility_if_guarded()

        self.assertTrue(restored)
        self.assertTrue(dock.preview_popup.isVisible())
        self.assertEqual(dock.preview_popup_opacity.values[-1], 1.0)
        self.assertEqual(dock.calls, ["refresh"])

    def test_restore_preview_visibility_if_guarded_does_not_reopen_without_active_guard(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _preview_visibility_guard_active = main_window.CustomDock._preview_visibility_guard_active
            _restore_preview_visibility_if_guarded = main_window.CustomDock._restore_preview_visibility_if_guarded

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=False)
                self.preview_popup_opacity = _FakeOpacityEffect()
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Two"}]
                self.pending_preview_item = None
                self.preview_visibility_guard_deadline = 5.0
                self.visible = True
                self.calls = []

            def isVisible(self):
                return self.visible

            def show(self):
                self.visible = True
                self.calls.append("show")

            def setWindowOpacity(self, value):
                self.calls.append(("opacity", value))

            def refresh_active_preview(self):
                self.calls.append("refresh")

        dock = _DummyDock()
        with mock.patch.object(main_window.time, "monotonic", return_value=10.0):
            restored = dock._restore_preview_visibility_if_guarded()

        self.assertFalse(restored)
        self.assertFalse(dock.preview_popup.isVisible())
        self.assertEqual(dock.calls, [])

    def test_event_filter_hides_dock_on_focus_loss_when_preview_guard_is_not_active(self):
        scheduled = {}

        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _preview_visibility_guard_active = main_window.CustomDock._preview_visibility_guard_active
            eventFilter = main_window.CustomDock.eventFilter
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Two"}]
                self.pending_preview_item = None
                self.preview_visibility_guard_deadline = 5.0
                self._suppress_preview_restore = False

            def hide_dock(self):
                scheduled["hide_called"] = True

        dock = _DummyDock()
        with (
            mock.patch.object(main_window.time, "monotonic", return_value=10.0),
            mock.patch.object(
                main_window.QTimer,
                "singleShot",
                side_effect=lambda delay, callback: scheduled.update({"delay": delay, "callback": callback}),
            ),
        ):
            handled = dock.eventFilter(dock, QEvent(QEvent.WindowDeactivate))

        self.assertFalse(handled)
        self.assertEqual(scheduled["delay"], 0)
        self.assertIs(scheduled["callback"].__self__, dock)
        self.assertEqual(scheduled["callback"].__func__, dock.hide_dock.__func__)

    def test_refresh_active_preview_hides_preview_when_item_is_gone(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _preview_title_for_window = main_window.CustomDock._preview_title_for_window
            _sync_preview_entries_from_running_windows = main_window.CustomDock._sync_preview_entries_from_running_windows
            refresh_active_preview = main_window.CustomDock.refresh_active_preview
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.preview_refresh_grace_attempts = 0
                self.current_preview_entries = []
                self.hide_preview_calls = 0

            def _find_dock_item_by_app_path(self, app_path):
                self.seen_app_path = app_path
                return None

            def _running_windows_by_id(self):
                return {}

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        dock.refresh_active_preview()

        self.assertEqual(dock.seen_app_path, "/tmp/focused.desktop")
        self.assertEqual(dock.hide_preview_calls, 1)

    def test_refresh_active_preview_keeps_panel_when_matching_dock_item_is_temporarily_missing(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _preview_title_for_window = main_window.CustomDock._preview_title_for_window
            _sync_preview_entries_from_running_windows = main_window.CustomDock._sync_preview_entries_from_running_windows
            refresh_active_preview = main_window.CustomDock.refresh_active_preview

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.active_preview_item = None
                self.current_preview_entries = [
                    {
                        "win_id": "0x2b",
                        "title": "Focused App\nWindow Two",
                        "app_data": {"Name": "Focused App", "path": "/tmp/focused.desktop"},
                    }
                ]
                self.preview_refresh_grace_attempts = 0
                self.hide_preview_calls = 0

            def _running_windows_by_id(self):
                return {"2b": {"id": "0x2b", "title": "Window Two"}}

            def _find_dock_item_by_app_path(self, app_path):
                self.seen_app_path = app_path
                return None

            def is_window_maximized(self, _win_id):
                return False

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        dock.refresh_active_preview()

        self.assertEqual(dock.seen_app_path, "/tmp/focused.desktop")
        self.assertEqual(dock.hide_preview_calls, 0)

    def test_refresh_active_preview_reuses_running_dock_item_when_sync_entries_disappear(self):
        anchor_item = _FakeDockPreviewItem()

        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _preview_title_for_window = main_window.CustomDock._preview_title_for_window
            _sync_preview_entries_from_running_windows = main_window.CustomDock._sync_preview_entries_from_running_windows
            refresh_active_preview = main_window.CustomDock.refresh_active_preview

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.active_preview_item = anchor_item
                self.current_preview_entries = [
                    {
                        "win_id": "0x2a",
                        "title": "Focused App\nWindow One",
                        "app_data": {"Name": "Focused App", "path": "/tmp/focused.desktop"},
                    }
                ]
                self.preview_refresh_grace_attempts = 0
                self.hide_preview_calls = 0
                self.show_preview_calls = []

            def _running_windows_by_id(self):
                return {}

            def _find_dock_item_by_app_path(self, app_path):
                self.seen_app_path = app_path
                return anchor_item

            def show_preview_for_item(self, dock_item, *, preserve_on_empty=False):
                self.show_preview_calls.append((dock_item, preserve_on_empty))
                return True

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        dock.refresh_active_preview()

        self.assertEqual(dock.seen_app_path, "/tmp/focused.desktop")
        self.assertEqual(dock.hide_preview_calls, 0)
        self.assertEqual(dock.show_preview_calls, [(anchor_item, True)])

    def test_show_preview_for_item_preserves_existing_entries_while_rebuilt_item_is_not_visible(self):
        item = _FakeDockPreviewItem()

        class _HiddenDockItem(_FakeDockPreviewItem):
            def isVisible(self):
                return False

        hidden_item = _HiddenDockItem()

        class _DummyDock:
            show_preview_for_item = main_window.CustomDock.show_preview_for_item
            _dock_item_app_path = main_window.CustomDock._dock_item_app_path
            _log_preview_hide_reason = staticmethod(lambda *_args, **_kwargs: None)

            def __init__(self):
                self.current_preview_entries = [{"win_id": "0x2a", "title": "Old"}]
                self.hide_preview_calls = 0

            def set_settings_revealed(self, _revealed):
                return None

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "isValid", return_value=True):
            shown = dock.show_preview_for_item(hidden_item, preserve_on_empty=True)

        self.assertFalse(shown)
        self.assertEqual(dock.hide_preview_calls, 0)
        self.assertEqual(dock.current_preview_entries, [{"win_id": "0x2a", "title": "Old"}])

    def test_remove_preview_entry_keeps_remaining_preview_cards_visible(self):
        anchor_item = _FakeDockPreviewItem()

        class _DummyDock:
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _remove_preview_entry = main_window.CustomDock._remove_preview_entry

            def __init__(self):
                self.current_preview_entries = [
                    {"win_id": "0x2a", "title": "One"},
                    {"win_id": "0x2b", "title": "Two"},
                ]
                self.active_preview_item = anchor_item
                self.active_preview_app_path = anchor_item.app_data["path"]
                self.preview_refresh_grace_attempts = 0
                self.rendered_entries = None
                self.hide_preview_calls = 0

            def _render_preview_entries(self, dock_item, previews):
                self.rendered_item = dock_item
                self.rendered_entries = [dict(preview) for preview in previews]
                self.current_preview_entries = [dict(preview) for preview in previews]

            def _find_dock_item_by_app_path(self, _app_path):
                return None

            def hide_preview(self):
                self.hide_preview_calls += 1

        dock = _DummyDock()
        with mock.patch.object(main_window, "isValid", return_value=True):
            dock._remove_preview_entry("0x2a")

        self.assertIs(dock.rendered_item, anchor_item)
        self.assertEqual(dock.rendered_entries, [{"win_id": "0x2b", "title": "Two"}])
        self.assertEqual(dock.preview_refresh_grace_attempts, 3)
        self.assertEqual(dock.hide_preview_calls, 0)

    def test_render_preview_entries_keeps_existing_panel_visible_without_refade(self):
        anchor_item = _FakeDockPreviewItem()

        class _DummyDock:
            _render_preview_entries = main_window.CustomDock._render_preview_entries
            _position_preview_popup = main_window.CustomDock._position_preview_popup

            def __init__(self):
                self.preview_popup = _FakePopupSizer()
                self.preview_host = _FakePreviewHost()
                self.preview_popup_fade = _FakeFadeAnimation()
                self.preview_popup_opacity = _FakeOpacityEffect()
                self.current_preview_entries = [{"win_id": "0x2a", "title": "Old"}]

            def adjustSize(self):
                self.adjusted = True

            def recenter(self):
                self.recentered = True

            def _preview_screen_geometry_for_item(self, _dock_item):
                return QRect(0, 0, 1920, 1080)

            def _log_window_state(self, *_args, **_kwargs):
                return None

        dock = _DummyDock()
        dock._render_preview_entries(anchor_item, [{"win_id": "0x2b", "title": "New", "pixmap": _FakePixmap(is_null=False)}])

        self.assertEqual(dock.preview_popup_fade.start_calls, 0)
        self.assertEqual(dock.preview_popup_opacity.values[-1], 1.0)
        self.assertEqual(dock.current_preview_entries[0]["win_id"], "0x2b")
        self.assertTrue(dock.preview_popup.animate_changes)

    def test_refresh_active_preview_retries_before_hiding_when_grace_is_available(self):
        scheduled = {}

        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _normalize_window_id = main_window.CustomDock._normalize_window_id
            _preview_title_for_window = main_window.CustomDock._preview_title_for_window
            _sync_preview_entries_from_running_windows = main_window.CustomDock._sync_preview_entries_from_running_windows
            refresh_active_preview = main_window.CustomDock.refresh_active_preview

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.preview_refresh_grace_attempts = 2
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Two"}]
                self.hide_preview_calls = 0

            def _running_windows_by_id(self):
                return {}

            def _find_dock_item_by_app_path(self, app_path):
                self.seen_app_path = app_path
                return None

            def hide_preview(self):
                self.hide_preview_calls += 1

            def refresh_preview_after_action(self):
                scheduled["callback_called"] = True

        dock = _DummyDock()
        with mock.patch.object(
            main_window.QTimer,
            "singleShot",
            side_effect=lambda delay, callback: scheduled.update({"delay": delay, "callback": callback}),
        ):
            dock.refresh_active_preview()

        self.assertEqual(dock.seen_app_path, "/tmp/focused.desktop")
        self.assertEqual(dock.preview_refresh_grace_attempts, 1)
        self.assertEqual(dock.hide_preview_calls, 0)
        self.assertEqual(scheduled["delay"], 180)
        self.assertIs(scheduled["callback"].__self__, dock)

    def test_check_mouse_proximity_restores_hidden_preview_while_guard_is_active(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            check_mouse_proximity = main_window.CustomDock.check_mouse_proximity

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=False)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Two"}]
                self.pending_preview_item = None
                self.preview_item_activation_guard_deadline = 0.0
                self.is_visible = True
                self.restore_calls = 0
                self.hide_preview_calls = 0

            def _screen_geometry(self, *, prefer_cursor=False):
                return _FakeScreenBounds()

            def _restore_preview_visibility_if_guarded(self):
                self.restore_calls += 1
                return True

            def _pointer_pressed_outside_dock(self, _pos):
                return False

            def hide_preview(self):
                self.hide_preview_calls += 1

            def update_settings_reveal(self, _pos):
                return None

        dock = _DummyDock()
        with mock.patch.object(main_window.QCursor, "pos", return_value=QPoint(400, 400)):
            dock.check_mouse_proximity()

        self.assertEqual(dock.restore_calls, 1)
        self.assertEqual(dock.hide_preview_calls, 0)

    def test_check_mouse_proximity_ignores_outside_click_while_preview_action_guard_is_active(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            check_mouse_proximity = main_window.CustomDock.check_mouse_proximity

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = [{"win_id": "0x2b", "title": "Two"}]
                self.pending_preview_item = None
                self.preview_item_activation_guard_deadline = 15.0
                self.is_visible = True
                self.hide_preview_calls = 0
                self.settings_updates = []

            def _screen_geometry(self, *, prefer_cursor=False):
                return _FakeScreenBounds()

            def _pointer_pressed_outside_dock(self, _pos):
                return True

            def hide_preview(self):
                self.hide_preview_calls += 1

            def update_settings_reveal(self, pos):
                self.settings_updates.append((pos.x(), pos.y()))

        dock = _DummyDock()
        with (
            mock.patch.object(main_window.QCursor, "pos", return_value=QPoint(400, 400)),
            mock.patch.object(main_window.time, "monotonic", return_value=10.0),
        ):
            dock.check_mouse_proximity()

        self.assertEqual(dock.hide_preview_calls, 0)
        self.assertEqual(dock.settings_updates, [(400, 400)])

    def test_pointer_pressed_outside_dock_detects_new_external_click(self):
        class _DummyDock:
            _global_pointer_pressed = main_window.CustomDock._global_pointer_pressed
            _pointer_pressed_outside_dock = main_window.CustomDock._pointer_pressed_outside_dock

            def __init__(self):
                self._last_mouse_buttons = Qt.MouseButton.NoButton

            def _dock_global_rect(self):
                return QRect(0, 0, 100, 100)

            def _preview_global_rect(self):
                return QRect()

        dock = _DummyDock()
        with mock.patch.object(main_window, "pointer_buttons_pressed_via_xlib", return_value=True):
            self.assertTrue(dock._pointer_pressed_outside_dock(QPoint(160, 160)))
            self.assertFalse(dock._pointer_pressed_outside_dock(QPoint(160, 160)))

    def test_check_mouse_proximity_keeps_dock_visible_while_preview_is_open(self):
        class _DummyDock:
            _has_active_preview_state = main_window.CustomDock._has_active_preview_state
            _preview_item_activation_guard_active = main_window.CustomDock._preview_item_activation_guard_active
            check_mouse_proximity = main_window.CustomDock.check_mouse_proximity

            def __init__(self):
                self.preview_popup = _FakePreviewPopup(visible=True)
                self.is_visible = True
                self.active_preview_app_path = "/tmp/focused.desktop"
                self.current_preview_entries = []
                self.pending_preview_item = None
                self.preview_item_activation_guard_deadline = 0.0
                self.hide_dock_calls = 0
                self.hide_preview_calls = 0
                self.settings_updates = []

            def _screen_geometry(self, *, prefer_cursor=False):
                self.prefer_cursor = prefer_cursor
                return _FakeScreenBounds()

            def _pointer_pressed_outside_dock(self, _pos):
                return False

            def show_dock(self):
                raise AssertionError("show_dock should not be called when the dock is already visible")

            def hide_dock(self):
                self.hide_dock_calls += 1

            def hide_preview(self):
                self.hide_preview_calls += 1

            def update_settings_reveal(self, pos):
                self.settings_updates.append((pos.x(), pos.y()))

        dock = _DummyDock()
        with mock.patch.object(main_window.QCursor, "pos", return_value=QPoint(400, 400)):
            dock.check_mouse_proximity()

        self.assertEqual(dock.hide_dock_calls, 0)
        self.assertEqual(dock.hide_preview_calls, 0)
        self.assertEqual(dock.settings_updates, [(400, 400)])

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
                self.x11_window_manager = mock.Mock()

            def _screen_geometry(self, *, prefer_cursor=False):
                self.prefer_cursor = prefer_cursor
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

    def test_dock_visual_settings_include_preview_panel_appearance_fields(self):
        settings = DockVisualSettings.from_mapping(
            {
                "preview_background_color": "#112233aa",
                "preview_background_image_path": "~/Pictures/preview.png",
                "preview_background_image_opacity": 67,
                "preview_background_image_fit": "contain",
                "preview_background_tint": "#44556677",
                "preview_border_color": "#abcdef",
                "preview_border_width": 3,
                "preview_border_radius": 22,
                "preview_border_style": "dashed",
            }
        )

        self.assertEqual(settings.preview_background_color, "#112233aa")
        self.assertTrue(settings.preview_background_image_path.endswith("/Pictures/preview.png"))
        self.assertEqual(settings.preview_background_image_opacity, 67)
        self.assertEqual(settings.preview_background_image_fit, "contain")
        self.assertEqual(settings.preview_background_tint, "#44556677")
        self.assertEqual(settings.preview_border_color, "#abcdef")
        self.assertEqual(settings.preview_border_width, 3)
        self.assertEqual(settings.preview_border_radius, 22)
        self.assertEqual(settings.preview_border_style, "dashed")
        self.assertIn("preview_background_image_path", settings.to_mapping())
        self.assertIn("preview_border_radius", settings.to_mapping())

    def test_apply_dock_settings_applies_preview_popup_settings(self):
        settings = DockVisualSettings.from_mapping(
            {
                "dock_padding": 14,
                "icon_size": 48,
                "icon_opacity": 76,
                "preview_background_color": "#334455ee",
                "preview_border_radius": 19,
            }
        )

        class _FakePanel:
            def __init__(self):
                self.applied = []

            def apply_settings(self, value):
                self.applied.append(value)

        class _FakeBoxLayout:
            def __init__(self):
                self.contents_margins = []
                self.spacings = []

            def setContentsMargins(self, left, top, right, bottom):
                self.contents_margins.append((left, top, right, bottom))

            def setSpacing(self, value):
                self.spacings.append(value)

        class _FakeButton:
            def __init__(self):
                self.fixed_sizes = []
                self.icon_sizes = []
                self._width = 0

            def setFixedSize(self, width, height):
                self.fixed_sizes.append((width, height))
                self._width = width

            def setIconSize(self, size):
                self.icon_sizes.append((size.width(), size.height()))

            def width(self):
                return self._width

        class _FakeSettingsPanel:
            def __init__(self):
                self.maximum_widths = []

            def setMaximumWidth(self, width):
                self.maximum_widths.append(width)

        class _DummyDock:
            apply_dock_settings = main_window.CustomDock.apply_dock_settings

            def __init__(self):
                self.container = _FakePanel()
                self.preview_popup = _FakePanel()
                self.container_layout = _FakeBoxLayout()
                self.app_row_layout = _FakeBoxLayout()
                self.settings_button = _FakeButton()
                self.settings_panel = _FakeSettingsPanel()
                self.settings_revealed = False
                self.last_dock_state = ["stale"]
                self.update_dock_items_calls = 0
                self.adjust_size_calls = 0
                self.recenter_calls = 0

            def update_dock_items(self):
                self.update_dock_items_calls += 1

            def adjustSize(self):
                self.adjust_size_calls += 1

            def recenter(self):
                self.recenter_calls += 1

            def _log_window_state(self, *_args, **_kwargs):
                return None

        dock = _DummyDock()
        with (
            mock.patch.object(main_window, "load_dock_settings", return_value=settings),
            mock.patch.object(main_window, "apply_widget_opacity") as apply_opacity,
        ):
            dock.apply_dock_settings()

        self.assertEqual(dock.container.applied, [settings])
        self.assertEqual(dock.preview_popup.applied, [settings])
        self.assertEqual(dock.container_layout.contents_margins[-1], (14, 14, 14, 14))
        self.assertEqual(dock.app_row_layout.spacings[-1], 7)
        self.assertEqual(dock.settings_button.fixed_sizes[-1], (66, 66))
        self.assertEqual(dock.settings_panel.maximum_widths[-1], 0)
        self.assertEqual(dock.last_dock_state, [])
        self.assertEqual(dock.update_dock_items_calls, 1)
        self.assertEqual(dock.adjust_size_calls, 1)
        self.assertEqual(dock.recenter_calls, 1)
        apply_opacity.assert_called_once_with(dock.settings_button, 76)

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
