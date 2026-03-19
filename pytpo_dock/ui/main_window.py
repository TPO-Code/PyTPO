from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import time

from PySide6.QtCore import QEasingCurve, QAbstractAnimation, QEvent, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QMenu, QToolButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from ..apps import (
    DESKTOP_APP_DRAG_MIME_TYPE,
    build_app_registry,
    build_runtime_window_app,
    launch_app,
    parse_desktop_app_drag_payload,
    parse_desktop_file,
)
from ..debug import dock_debug_enabled, log_dock_debug
from ..match_diagnostics import write_window_snapshot
from ..settings_dialog import DockSettingsDialog, load_dock_settings
from ..storage_paths import dock_pinned_apps_path, migrate_legacy_dock_storage
from ..window_matching import finalize_window_records, match_threshold, runtime_group_path, score_window_match
from ..x11_dock_window import X11DockWindowManager
from ..xlib_window_source import list_windows_via_xlib, pointer_buttons_pressed_via_xlib
from ..x11_window_preview import X11WindowPreviewCapturer
from .widgets import DockContainerFrame, DockItem, WindowPreview, apply_widget_opacity, build_settings_icon


class CustomDock(QWidget):
    def __init__(self):
        super().__init__()
        migrate_legacy_dock_storage()

        platform_name = QApplication.instance().platformName().lower() if QApplication.instance() is not None else ""
        window_flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        if platform_name == "xcb":
            window_flags |= Qt.X11BypassWindowManagerHint
        self.setWindowFlags(window_flags)
        if platform_name == "xcb":
            self.setAttribute(Qt.WA_X11NetWmWindowTypeDock, True)
            self.setAttribute(Qt.WA_X11DoNotAcceptFocus, True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.setAcceptDrops(True)
        self.x11_window_manager = X11DockWindowManager(self)

        self.registry = build_app_registry()
        self.load_pinned_apps()
        self.dock_settings = load_dock_settings()
        self.anim = None

        self.is_visible = False
        self.last_dock_state = []
        self.pending_preview_item = None
        self.preview_host = QWidget(self)
        self.preview_host.setFixedSize(0, 0)
        preview_window_flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
        if platform_name == "xcb":
            preview_window_flags |= Qt.X11BypassWindowManagerHint
        self.preview_popup = WindowPreview()
        self.preview_popup.setWindowFlags(preview_window_flags)
        self.preview_popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.preview_popup.setAttribute(Qt.WA_TranslucentBackground, True)
        if platform_name == "xcb":
            self.preview_popup.setAttribute(Qt.WA_X11DoNotAcceptFocus, True)
        self.preview_popup.hide()
        self.preview_popup_opacity = QGraphicsOpacityEffect(self.preview_popup)
        self.preview_popup_opacity.setOpacity(1.0)
        self.preview_popup.setGraphicsEffect(self.preview_popup_opacity)
        self.preview_popup_fade = QPropertyAnimation(self.preview_popup_opacity, b"opacity", self)
        self.preview_popup_fade.setDuration(300)
        self.preview_popup_fade.setEasingCurve(QEasingCurve.InOutCubic)
        self.preview_popup.interaction_started.connect(self.handle_preview_interaction_started)
        self.preview_popup.action_requested.connect(self.handle_preview_action)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.show_pending_preview)
        self.preview_hide_timer = QTimer(self)
        self.preview_hide_timer.setSingleShot(True)
        self.preview_hide_timer.timeout.connect(self.hide_preview_if_inactive)
        self.settings_revealed = False
        self.preview_hover_active = False
        self.active_preview_item = None
        self.active_preview_app_path = ""
        self.active_preview_anchor_rect = None
        self.current_preview_entries = []
        self.preview_refresh_grace_attempts = 0
        self.preview_visibility_guard_deadline = 0.0
        self.preview_item_activation_guard_deadline = 0.0
        self._suppress_preview_restore = False
        self._last_mouse_buttons = Qt.MouseButton.NoButton
        self._last_active_window_id = ""
        self.last_focused_windows = {}
        self.x11_preview_capturer = X11WindowPreviewCapturer()

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.container = DockContainerFrame(self)
        self.container.setObjectName("DockContainer")
        self.container.setAcceptDrops(True)
        self.container.installEventFilter(self)
        self.container_layout = QHBoxLayout(self.container)
        self.container_layout.setContentsMargins(10, 10, 10, 10)
        self.container_layout.setSpacing(5)

        self.settings_panel = QWidget(self.container)
        self.settings_panel.setMaximumWidth(0)
        self.settings_panel.setMinimumWidth(0)
        self.settings_panel_layout = QHBoxLayout(self.settings_panel)
        self.settings_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_panel_layout.setSpacing(0)

        self.settings_button = QToolButton(self.settings_panel)
        self.settings_button.setFixedSize(44, 44)
        self.settings_button.setIcon(build_settings_icon())
        self.settings_button.setIconSize(QSize(24, 24))
        self.settings_button.setToolTip("Dock settings")
        self.settings_button.setStyleSheet("""
            QToolButton {
                background: rgba(255, 255, 255, 20);
                border-radius: 12px;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 38);
            }
            QToolButton:pressed {
                background: rgba(255, 255, 255, 24);
            }
        """)
        self.settings_button.clicked.connect(self.show_settings_menu)
        self.settings_panel_layout.addWidget(self.settings_button)

        self.app_row = QWidget(self.container)
        self.app_row.setAcceptDrops(True)
        self.app_row.installEventFilter(self)
        self.app_row_layout = QHBoxLayout(self.app_row)
        self.app_row_layout.setContentsMargins(0, 0, 0, 0)
        self.app_row_layout.setSpacing(5)

        self.container_layout.addWidget(self.settings_panel)
        self.container_layout.addWidget(self.app_row)

        self.settings_anim = QPropertyAnimation(self.settings_panel, b"maximumWidth", self)
        self.settings_anim.setDuration(180)
        self.settings_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.settings_anim.valueChanged.connect(lambda *_: self.recenter())

        self.main_layout.addWidget(self.preview_host, 0, Qt.AlignHCenter)
        self.main_layout.addWidget(self.container, 0, Qt.AlignHCenter)
        self.preview_popup.installEventFilter(self)
        self.installEventFilter(self)

        self.mouse_timer = QTimer(self)
        self.mouse_timer.timeout.connect(self.check_mouse_proximity)
        self.mouse_timer.start(20)

        self.wm_timer = QTimer(self)
        self.wm_timer.timeout.connect(self.update_dock_items)
        self.wm_timer.start(1000)

        self.active_window_timer = QTimer(self)
        self.active_window_timer.timeout.connect(self.refresh_active_window_highlight)
        self.active_window_timer.start(250)

        self.apply_dock_settings()
        self.update_dock_items()
        self.refresh_active_window_highlight(force=True)
        self.recenter()
        self._log_window_state("dock-init-complete", pinned_apps=list(self.pinned_apps))

    def _log_window_state(self, event_name, /, **fields):
        log_dock_debug(
            event_name,
            visible=self.isVisible(),
            is_visible_flag=self.is_visible,
            geometry=self.geometry().getRect(),
            frame_geometry=self.frameGeometry().getRect(),
            pos=(self.x(), self.y()),
            size=(self.width(), self.height()),
            container_size=(self.container.width(), self.container.height()),
            preview_host_size=(self.preview_host.width(), self.preview_host.height()),
            settings_revealed=self.settings_revealed,
            **fields,
        )

    def _screen_geometry(self, *, prefer_cursor=False):
        screen = None
        if prefer_cursor:
            screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = self.screen()
        if screen is None:
            frame_geometry = self.frameGeometry()
            if not frame_geometry.isNull():
                screen = QGuiApplication.screenAt(frame_geometry.center())
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.geometry() if screen is not None else QRect()

    def ensure_hidden_window_mapped(self):
        screen_geometry = self._screen_geometry(prefer_cursor=True)
        if screen_geometry.isNull():
            return
        hidden_x = screen_geometry.x() + (screen_geometry.width() - self.width()) // 2
        hidden_y = self._hidden_y(screen_geometry)
        self.move(hidden_x, hidden_y)
        self.setWindowOpacity(0.0 if self._visibility_animation_mode() == "fade" else 1.0)
        if not self.isVisible():
            self.show()
        self.x11_window_manager.sync(
            reserve_space=False,
            window_rect=QRect(hidden_x, hidden_y, self.width(), self.height()),
        )
        self._log_window_state(
            "dock-hidden-window-mapped",
            screen_geometry=screen_geometry.getRect(),
            target_pos=(hidden_x, hidden_y),
        )

    def _visible_y(self, screen_geometry):
        return screen_geometry.y() + screen_geometry.height() - self.height() - 15

    def _hidden_y(self, screen_geometry):
        overshoot = max(12, int(self.height() * 0.25))
        return screen_geometry.y() + screen_geometry.height() + overshoot

    def _normalize_window_id(self, win_id):
        if isinstance(win_id, int):
            return format(win_id, "x")
        try:
            return format(int(str(win_id), 0), "x")
        except (TypeError, ValueError):
            return ""

    def _normalize_desktop_path(self, desktop_path):
        normalized = os.path.abspath(os.path.expanduser(str(desktop_path or "").strip()))
        if not normalized.lower().endswith(".desktop"):
            return ""
        return normalized

    def _valid_desktop_app_path(self, desktop_path):
        normalized = self._normalize_desktop_path(desktop_path)
        if not normalized or not os.path.isfile(normalized):
            return ""
        app_data = parse_desktop_file(normalized)
        if app_data.get("Type") != "Application" or not app_data.get("Name"):
            return ""
        return normalized

    def _desktop_path_from_mime_data(self, mime_data):
        if mime_data is None:
            return ""
        if mime_data.hasFormat(DESKTOP_APP_DRAG_MIME_TYPE):
            payload = bytes(mime_data.data(DESKTOP_APP_DRAG_MIME_TYPE))
            desktop_path = self._valid_desktop_app_path(parse_desktop_app_drag_payload(payload))
            if desktop_path:
                return desktop_path
        if mime_data.hasUrls():
            for url in mime_data.urls():
                if not url.isLocalFile():
                    continue
                desktop_path = self._valid_desktop_app_path(url.toLocalFile())
                if desktop_path:
                    return desktop_path
        if mime_data.hasText():
            return self._valid_desktop_app_path(mime_data.text())
        return ""

    def _set_drop_active(self, is_active):
        self.container.set_drop_active(is_active)

    def _pin_dropped_app(self, desktop_path):
        if not desktop_path or desktop_path in self.pinned_apps:
            return False
        self.pinned_apps.append(desktop_path)
        self.save_pinned_apps()
        self.update_dock_items()
        return True

    def _accept_app_drag(self, event):
        desktop_path = self._desktop_path_from_mime_data(event.mimeData())
        if not desktop_path:
            self._set_drop_active(False)
            event.ignore()
            return ""
        if not self.is_visible:
            self.show_dock()
        if self.preview_popup.isVisible() or self.pending_preview_item is not None:
            self._log_preview_hide_reason("accept-app-drag")
            self.hide_preview()
        self.set_settings_revealed(False)
        self._set_drop_active(True)
        event.setDropAction(Qt.CopyAction)
        event.acceptProposedAction()
        return desktop_path

    def eventFilter(self, watched, event):
        event_type = event.type()
        if watched in {self, self.preview_popup} and event_type in {
            QEvent.Hide,
            QEvent.Close,
            QEvent.WindowDeactivate,
            QEvent.FocusOut,
        }:
            if self._has_active_preview_state() and not getattr(self, "_suppress_preview_restore", False):
                if self._preview_visibility_guard_active():
                    QTimer.singleShot(0, self._restore_preview_visibility_if_guarded)
                else:
                    self._log_preview_hide_reason(
                        "event-filter-focus-loss",
                        watched="preview" if watched is self.preview_popup else "dock",
                        event_type=int(event_type),
                    )
                    QTimer.singleShot(0, self.hide_dock)
            return False
        if event_type == QEvent.DragEnter:
            self.dragEnterEvent(event)
            return event.isAccepted()
        if event_type == QEvent.DragMove:
            self.dragMoveEvent(event)
            return event.isAccepted()
        if event_type == QEvent.DragLeave:
            self.dragLeaveEvent(event)
            return event.isAccepted()
        if event_type == QEvent.Drop:
            self.dropEvent(event)
            return event.isAccepted()
        return super().eventFilter(watched, event)

    def _run_window_command(self, args):
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def _xdotool_window_id(self, win_id):
        try:
            return str(int(str(win_id), 0))
        except (TypeError, ValueError):
            return ""

    def _is_own_window(self, win_id):
        own_id = self.winId()
        if not own_id:
            return False
        return self._normalize_window_id(win_id) == self._normalize_window_id(own_id)

    def _remember_window_focus(self, app_path, win_id):
        if app_path and win_id:
            self.last_focused_windows[str(app_path)] = self._normalize_window_id(win_id)

    def _active_window_id(self):
        try:
            output = subprocess.check_output(
                ['xprop', '-root', '_NET_ACTIVE_WINDOW'],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return ""
        match = re.search(r'window id # (0x[0-9a-fA-F]+)', output)
        if not match:
            return ""
        return self._normalize_window_id(match.group(1))

    def _primary_window_for_item(self, dock_item):
        windows = dock_item.windows or []
        if not windows and dock_item.win_id:
            windows = [{'id': dock_item.win_id, 'title': dock_item.app_data.get('Title', '')}]
        if not windows:
            return None

        active_window_id = self._active_window_id()
        app_path = str(dock_item.app_data.get('path') or '')
        remembered_window_id = self.last_focused_windows.get(app_path, "")

        for window in windows:
            if self._normalize_window_id(window.get('id')) == active_window_id:
                return window
        for window in windows:
            if self._normalize_window_id(window.get('id')) == remembered_window_id:
                return window
        return windows[0]

    def _item_action_payload(self, dock_item):
        target_window = self._primary_window_for_item(dock_item)
        win_id = target_window.get('id') if target_window else dock_item.win_id
        return {
            'win_id': win_id,
            'app_data': dock_item.app_data,
            'app_path': dock_item.app_data.get('path'),
            'is_maximized': self.is_window_maximized(win_id),
        }

    def _dock_item_app_path(self, dock_item) -> str:
        if dock_item is None:
            return ""
        return str(getattr(dock_item, "app_data", {}).get("path") or "").strip()

    def _iter_dock_items(self):
        for index in range(self.app_row_layout.count()):
            layout_item = self.app_row_layout.itemAt(index)
            dock_item = layout_item.widget() if layout_item is not None else None
            if dock_item is not None:
                yield dock_item

    def _find_dock_item_by_app_path(self, app_path: str):
        normalized_path = str(app_path or "").strip()
        if not normalized_path:
            return None
        for dock_item in self._iter_dock_items():
            if self._dock_item_app_path(dock_item) == normalized_path:
                return dock_item
        return None

    def _dock_item_matches_active_window(self, dock_item, normalized_active_window_id):
        normalized_active_window_id = str(normalized_active_window_id or "").strip().lower()
        if not normalized_active_window_id:
            return False
        windows = list(getattr(dock_item, "windows", None) or [])
        if not windows and getattr(dock_item, "win_id", None):
            windows = [{"id": getattr(dock_item, "win_id", None)}]
        for window in windows:
            if self._normalize_window_id(window.get("id")) == normalized_active_window_id:
                return True
        return False

    def refresh_active_window_highlight(self, force: bool = False):
        normalized_active_window_id = self._active_window_id()
        if not force and normalized_active_window_id == self._last_active_window_id:
            return
        self._last_active_window_id = normalized_active_window_id

        preview_item = None
        preview_matches_active_window = False
        if self._has_active_preview_state() and self.active_preview_app_path:
            preview_item = self._find_dock_item_by_app_path(self.active_preview_app_path)
            if preview_item is not None:
                preview_matches_active_window = self._dock_item_matches_active_window(
                    preview_item,
                    normalized_active_window_id,
                )

        if (
            self._has_active_preview_state()
            and normalized_active_window_id
            and not self._is_own_window(normalized_active_window_id)
        ):
            if self._preview_item_activation_guard_active():
                log_dock_debug(
                    "dock-preview-focus-hide-suppressed",
                    active_window_id=normalized_active_window_id,
                    app_path=self.active_preview_app_path,
                    preview_matches_active_window=preview_matches_active_window,
                )
            else:
                self._log_preview_hide_reason(
                    "active-window-focus-left-dock",
                    active_window_id=normalized_active_window_id,
                )
                self.hide_dock()
                return

        active_app_names: list[str] = []
        for index in range(self.app_row_layout.count()):
            layout_item = self.app_row_layout.itemAt(index)
            dock_item = layout_item.widget() if layout_item is not None else None
            if dock_item is None or not hasattr(dock_item, "set_active_window"):
                continue
            is_active_window = self._dock_item_matches_active_window(dock_item, normalized_active_window_id)
            dock_item.set_active_window(is_active_window)
            if is_active_window:
                active_app_names.append(str(getattr(dock_item, "app_data", {}).get("Name", "Unknown App")))

        log_dock_debug(
            "dock-active-window-highlight-updated",
            active_window_id=normalized_active_window_id,
            active_apps=active_app_names,
        )

    def _stop_animation(self):
        if self.anim is not None and self.anim.state() == QAbstractAnimation.Running:
            self.anim.stop()

    def _visibility_animation_mode(self) -> str:
        mode = str(getattr(self.dock_settings, "visibility_animation_mode", "fade") or "").strip().lower()
        return mode if mode in {"fade", "slide"} else "fade"

    def _preview_delay_ms(self) -> int:
        return 300

    def _is_visibility_animation_running(self) -> bool:
        return bool(self.anim is not None and self.anim.state() == QAbstractAnimation.Running)

    def _cursor_over_widget(self, widget) -> bool:
        if widget is None or not isValid(widget) or not widget.isVisible():
            return False
        widget_rect = QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())
        return widget_rect.contains(QCursor.pos())

    def _restart_deferred_preview_if_hovered(self):
        dock_item = self.pending_preview_item
        if not self._cursor_over_widget(dock_item):
            return
        self.preview_timer.start(self._preview_delay_ms())

    def _has_active_preview_state(self) -> bool:
        return bool(
            getattr(self, "active_preview_app_path", "")
            or getattr(self, "current_preview_entries", [])
            or getattr(self, "pending_preview_item", None)
        )

    def _arm_preview_visibility_guard(self, seconds: float = 0.5) -> None:
        self.preview_visibility_guard_deadline = max(
            self.preview_visibility_guard_deadline,
            time.monotonic() + max(0.0, float(seconds)),
        )

    def _preview_visibility_guard_active(self) -> bool:
        return time.monotonic() < self.preview_visibility_guard_deadline

    def _arm_preview_item_activation_guard(self, seconds: float = 0.35) -> None:
        self.preview_item_activation_guard_deadline = max(
            self.preview_item_activation_guard_deadline,
            time.monotonic() + max(0.0, float(seconds)),
        )

    def _preview_item_activation_guard_active(self) -> bool:
        return time.monotonic() < self.preview_item_activation_guard_deadline

    def _log_preview_hide_reason(self, reason: str, /, **fields) -> None:
        cursor_pos = QCursor.pos()
        log_dock_debug(
            "dock-preview-hide-reason",
            reason=reason,
            cursor_pos=(cursor_pos.x(), cursor_pos.y()),
            active_preview_app_path=self.active_preview_app_path,
            preview_count=len(self.current_preview_entries),
            popup_visible=self.preview_popup.isVisible(),
            dock_visible=self.isVisible(),
            **fields,
        )

    def _restore_preview_visibility_if_guarded(self) -> bool:
        if not self._has_active_preview_state():
            return False
        if not self._preview_visibility_guard_active():
            return False
        if not self.isVisible():
            self.show()
            self.setWindowOpacity(1.0)
        self.preview_popup.show()
        self.preview_popup_opacity.setOpacity(1.0)
        self.refresh_active_preview()
        return True

    def _preview_screen_geometry_for_item(self, dock_item) -> QRect:
        if dock_item is not None and isValid(dock_item):
            anchor_rect = QRect(dock_item.mapToGlobal(QPoint(0, 0)), dock_item.size())
            screen = QGuiApplication.screenAt(anchor_rect.center())
            if screen is not None:
                return screen.availableGeometry()
        return self._screen_geometry(prefer_cursor=True)

    def _preview_global_rect(self) -> QRect:
        if self.preview_popup is None or not self.preview_popup.isVisible():
            return QRect()
        return self.preview_popup.frameGeometry()

    def _dock_global_rect(self) -> QRect:
        return QRect(self.mapToGlobal(QPoint(0, 0)), self.size())

    def _global_pointer_pressed(self) -> bool:
        pointer_pressed = pointer_buttons_pressed_via_xlib()
        if pointer_pressed is not None:
            return bool(pointer_pressed)
        return QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton

    def _pointer_pressed_outside_dock(self, cursor_pos: QPoint) -> bool:
        pressed_now = self._global_pointer_pressed()
        was_pressed = bool(self._last_mouse_buttons)
        self._last_mouse_buttons = Qt.MouseButton.LeftButton if pressed_now else Qt.MouseButton.NoButton
        if not pressed_now or was_pressed:
            return False
        dock_rect = self._dock_global_rect()
        preview_rect = self._preview_global_rect()
        inside_dock = dock_rect.contains(cursor_pos)
        inside_preview = not preview_rect.isNull() and preview_rect.contains(cursor_pos)
        preview_popup = getattr(self, "preview_popup", None)
        log_dock_debug(
            "dock-preview-pointer-press",
            cursor_pos=(cursor_pos.x(), cursor_pos.y()),
            inside_dock=inside_dock,
            inside_preview=inside_preview,
            dock_rect=dock_rect.getRect(),
            preview_rect=preview_rect.getRect() if not preview_rect.isNull() else None,
            popup_visible=bool(preview_popup is not None and preview_popup.isVisible()),
            last_mouse_buttons=getattr(self._last_mouse_buttons, "value", self._last_mouse_buttons),
        )
        if inside_dock:
            return False
        if inside_preview:
            return False
        return True

    def _handle_visibility_animation_finished(self):
        if self.is_visible:
            self.setWindowOpacity(1.0)
            self._restart_deferred_preview_if_hovered()
            self._log_window_state("dock-show-animation-finished")
            return

        screen_geometry = self._screen_geometry()
        self.setWindowOpacity(0.0 if self._visibility_animation_mode() == "fade" else 1.0)
        self.move(self.x(), self._hidden_y(screen_geometry))
        self._log_window_state("dock-hide-animation-finished")

    def showEvent(self, event):
        super().showEvent(event)
        self.x11_window_manager.sync(reserve_space=self.is_visible)
        self._log_window_state("dock-show-event")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.x11_window_manager.sync(reserve_space=self.is_visible)
        self._log_window_state(
            "dock-resize-event",
            old_size=(event.oldSize().width(), event.oldSize().height()),
            new_size=(event.size().width(), event.size().height()),
        )

    def moveEvent(self, event):
        super().moveEvent(event)
        self.x11_window_manager.sync(reserve_space=self.is_visible)
        self._log_window_state(
            "dock-move-event",
            old_pos=(event.oldPos().x(), event.oldPos().y()),
            new_pos=(event.pos().x(), event.pos().y()),
        )

    def closeEvent(self, event):
        self.x11_window_manager.sync(reserve_space=False)
        self.preview_popup.hide()
        self.preview_popup.close()
        super().closeEvent(event)

    def load_pinned_apps(self):
        self.pinned_apps = []
        pinned_file = dock_pinned_apps_path()
        if not pinned_file.is_file():
            return
        try:
            with pinned_file.open('r', encoding='utf-8') as file_handle:
                self.pinned_apps = json.load(file_handle)
        except json.JSONDecodeError:
            pass

    def save_pinned_apps(self):
        with dock_pinned_apps_path().open('w', encoding='utf-8') as file_handle:
            json.dump(self.pinned_apps, file_handle)

    def handle_pin_toggle(self, desktop_path, should_pin):
        desktop_path = self._normalize_desktop_path(desktop_path) or str(desktop_path or "").strip()
        if should_pin and desktop_path not in self.pinned_apps:
            self.pinned_apps.append(desktop_path)
        elif not should_pin and desktop_path in self.pinned_apps:
            self.pinned_apps.remove(desktop_path)
        self.save_pinned_apps()
        self.update_dock_items()

    def dragEnterEvent(self, event: QDragEnterEvent):
        self._accept_app_drag(event)

    def dragMoveEvent(self, event: QDragMoveEvent):
        self._accept_app_drag(event)

    def dragLeaveEvent(self, event):
        self._set_drop_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent):
        desktop_path = self._accept_app_drag(event)
        self._set_drop_active(False)
        if not desktop_path:
            return
        was_pinned = self._pin_dropped_app(desktop_path)
        log_dock_debug("dock-app-dropped", desktop_path=desktop_path, pinned=was_pinned)
        event.acceptProposedAction()

    def get_running_windows(self):
        records = list_windows_via_xlib()
        return finalize_window_records(records, is_own_window=self._is_own_window)

    def _known_apps_by_path(self):
        known_apps: dict[str, dict[str, str]] = {}
        for path in self.pinned_apps:
            if not os.path.exists(path):
                continue
            app_data = parse_desktop_file(path)
            if app_data:
                known_apps[path] = app_data

        for app_data in self.registry.values():
            path = str(app_data.get('path') or '').strip()
            if path and path not in known_apps:
                known_apps[path] = app_data
        return known_apps

    def _assign_windows_to_apps(self, running_windows, known_apps_by_path):
        assigned_windows: dict[str, list[dict]] = {}
        matched_paths_in_order: list[str] = []
        unmatched_windows: list[dict] = []
        threshold = match_threshold()

        app_entries = [(path, app_data) for path, app_data in known_apps_by_path.items()]
        for window in running_windows:
            best_path = ""
            best_score = 0
            for path, app_data in app_entries:
                score = score_window_match(window, app_data)
                if score > best_score:
                    best_score = score
                    best_path = path

            if best_path and best_score >= threshold:
                if best_path not in assigned_windows:
                    assigned_windows[best_path] = []
                    matched_paths_in_order.append(best_path)
                assigned_windows[best_path].append(window)
                continue

            unmatched_windows.append(window)

        return assigned_windows, matched_paths_in_order, unmatched_windows

    def _runtime_window_groups(self, windows):
        groups: dict[str, list[dict]] = {}
        ordered_paths: list[str] = []
        for window in windows:
            path = runtime_group_path(window)
            if path not in groups:
                groups[path] = []
                ordered_paths.append(path)
            groups[path].append(window)
        return [(path, groups[path]) for path in ordered_paths]

    def update_dock_items(self, force_snapshot: bool = False):
        running_windows = self.get_running_windows()
        target_items = []
        added_paths = set()
        known_apps_by_path = self._known_apps_by_path()
        assigned_windows, matched_paths_in_order, unmatched_windows = self._assign_windows_to_apps(
            running_windows,
            known_apps_by_path,
        )

        for path in self.pinned_apps:
            app_data = known_apps_by_path.get(path)
            if not app_data:
                continue
            matching_windows = assigned_windows.get(path, [])
            target_items.append({
                'path': path,
                'data': app_data,
                'is_pinned': True,
                'is_running': bool(matching_windows),
                'win_id': matching_windows[0]['id'] if matching_windows else None,
                'windows': matching_windows,
            })
            added_paths.add(path)

        for path in matched_paths_in_order:
            if path in added_paths:
                continue
            app_data = known_apps_by_path.get(path)
            matching_windows = assigned_windows.get(path, [])
            if not app_data or not matching_windows:
                continue
            target_items.append({
                'path': path,
                'data': app_data,
                'is_pinned': False,
                'is_running': True,
                'win_id': matching_windows[0]['id'],
                'windows': matching_windows,
            })
            added_paths.add(path)

        for path, windows in self._runtime_window_groups(unmatched_windows):
            if path in added_paths or not windows:
                continue
            target_items.append({
                'path': path,
                'data': build_runtime_window_app(windows[0], path=path),
                'is_pinned': False,
                'is_running': True,
                'win_id': windows[0]['id'],
                'windows': windows,
            })
            added_paths.add(path)

        current_state_signature = [
            (
                item['path'],
                item['is_running'],
                tuple((window['id'], window.get('title', '')) for window in item.get('windows', [])),
            )
            for item in target_items
        ]
        if dock_debug_enabled() and (force_snapshot or current_state_signature != self.last_dock_state):
            self._write_window_snapshot(
                running_windows=running_windows,
                known_apps_by_path=known_apps_by_path,
                assigned_windows=assigned_windows,
                unmatched_windows=unmatched_windows,
                target_items=target_items,
            )
        if current_state_signature != self.last_dock_state:
            log_dock_debug(
                "dock-items-state-change",
                running_windows=len(running_windows),
                item_count=len(target_items),
                items=[
                    {
                        'path': item['path'],
                        'running': item['is_running'],
                        'window_count': len(item.get('windows', [])),
                    }
                    for item in target_items
                ],
            )
            self.rebuild_layout(target_items)
            self.last_dock_state = current_state_signature

    def _write_window_snapshot(
        self,
        *,
        running_windows,
        known_apps_by_path,
        assigned_windows,
        unmatched_windows,
        target_items,
    ):
        try:
            json_path, markdown_path = write_window_snapshot(
                running_windows=running_windows,
                known_apps_by_path=known_apps_by_path,
                assigned_windows=assigned_windows,
                unmatched_windows=unmatched_windows,
                target_items=target_items,
            )
        except Exception as exc:
            log_dock_debug("dock-window-snapshot-write-failed", error=repr(exc))
            return
        log_dock_debug(
            "dock-window-snapshot-written",
            json_path=json_path,
            markdown_path=markdown_path,
            running_windows=len(running_windows),
            item_count=len(target_items),
        )

    def rebuild_layout(self, items):
        preview_app_path = self.active_preview_app_path if self._has_active_preview_state() else ""

        while self.app_row_layout.count():
            item = self.app_row_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for item in items:
            button = DockItem(
                item['data'],
                item['is_pinned'],
                item['is_running'],
                item['win_id'],
                item.get('windows'),
                icon_size=self.dock_settings.icon_size,
                icon_opacity=self.dock_settings.icon_opacity,
                indicator_mode=self.dock_settings.instance_indicator_mode,
            )
            button.apply_visual_settings(self.dock_settings)
            button.setAcceptDrops(True)
            button.installEventFilter(self)
            button.pin_toggled.connect(self.handle_pin_toggle)
            button.activated.connect(self.handle_item_activation)
            button.context_menu_requested.connect(self.show_item_context_menu)
            self.app_row_layout.addWidget(button)

        self.adjustSize()
        self.refresh_active_window_highlight(force=True)
        self.recenter()
        if preview_app_path:
            self.active_preview_app_path = preview_app_path
            self.refresh_active_preview()
        self._log_window_state(
            "dock-layout-rebuilt",
            item_count=len(items),
            app_row_count=self.app_row_layout.count(),
            button_names=[item['data'].get('Name', 'Unknown App') for item in items],
        )

    def set_settings_revealed(self, revealed):
        if self.settings_revealed == revealed:
            return
        self.settings_revealed = revealed
        self._log_window_state("dock-settings-reveal-changed", revealed=revealed)
        self.settings_anim.stop()
        self.settings_anim.setStartValue(self.settings_panel.maximumWidth())
        self.settings_anim.setEndValue(self.settings_button.width() if revealed else 0)
        self.settings_anim.start()

    def show_settings_menu(self):
        menu = QMenu(self)
        settings_action = QAction("Dock Settings", self)
        menu.addAction(settings_action)

        menu.addSeparator()

        quit_action = QAction("Quit Dock", self)
        menu.addAction(quit_action)

        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(40, 40, 40, 240);
                color: white;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 40);
            }
        """)
        chosen_action = menu.exec(self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft()))
        if chosen_action == settings_action:
            self.open_settings_dialog()
        elif chosen_action == quit_action:
            QApplication.instance().quit()

    def open_settings_dialog(self):
        self._log_preview_hide_reason("open-settings-dialog")
        self.hide_preview()
        dialog = DockSettingsDialog(on_applied=self.apply_dock_settings, parent=self)
        dialog.exec()

    def reload_registry(self):
        self.registry = build_app_registry()
        self.update_dock_items()

    def apply_dock_settings(self):
        self.dock_settings = load_dock_settings()
        padding = max(0, int(self.dock_settings.dock_padding))
        spacing = max(4, padding // 2)
        button_size = max(48, int(self.dock_settings.icon_size) + 18)

        self.container.apply_settings(self.dock_settings)
        self.preview_popup.apply_settings(self.dock_settings)
        self.container_layout.setContentsMargins(padding, padding, padding, padding)
        self.container_layout.setSpacing(spacing)
        self.app_row_layout.setSpacing(spacing)
        self.settings_button.setFixedSize(button_size, button_size)
        self.settings_button.setIconSize(QSize(max(18, button_size - 20), max(18, button_size - 20)))
        apply_widget_opacity(self.settings_button, self.dock_settings.icon_opacity)
        self.settings_panel.setMaximumWidth(self.settings_button.width() if self.settings_revealed else 0)

        self.last_dock_state = []
        self.update_dock_items()
        self.adjustSize()
        self.recenter()
        self._log_window_state(
            "dock-settings-applied",
            padding=padding,
            spacing=spacing,
            button_size=button_size,
            visibility_animation_mode=self.dock_settings.visibility_animation_mode,
            icon_size=self.dock_settings.icon_size,
            icon_opacity=self.dock_settings.icon_opacity,
            hover_highlight_color=self.dock_settings.hover_highlight_color,
            hover_highlight_opacity=self.dock_settings.hover_highlight_opacity,
            focused_window_highlight_color=self.dock_settings.focused_window_highlight_color,
            focused_window_highlight_opacity=self.dock_settings.focused_window_highlight_opacity,
            background_image_opacity=self.dock_settings.background_image_opacity,
            preview_background_image_opacity=self.dock_settings.preview_background_image_opacity,
            preview_border_radius=self.dock_settings.preview_border_radius,
        )

    def schedule_preview(self, dock_item):
        if dock_item is None or not isValid(dock_item):
            return
        self.set_settings_revealed(False)
        self.pending_preview_item = dock_item
        self.active_preview_item = dock_item
        self.active_preview_anchor_rect = QRect(
            dock_item.mapToGlobal(QPoint(0, 0)),
            dock_item.size(),
        )
        self.preview_hide_timer.stop()
        if self._is_visibility_animation_running():
            self.preview_timer.stop()
            self._log_window_state(
                "dock-preview-deferred-for-animation",
                app_name=dock_item.app_data.get('Name', 'Unknown App'),
            )
            return
        self.preview_timer.start(self._preview_delay_ms())
        self._log_window_state(
            "dock-preview-scheduled",
            app_name=dock_item.app_data.get('Name', 'Unknown App'),
            dock_item_rect=self.active_preview_anchor_rect.getRect(),
            window_count=len(dock_item.windows or []),
        )

    def schedule_preview_hide(self):
        self.preview_hide_timer.start(120)
        self._log_window_state("dock-preview-hide-scheduled")

    def show_pending_preview(self):
        dock_item = self.pending_preview_item
        if dock_item is None or not isValid(dock_item) or not dock_item.isVisible():
            self._log_window_state("dock-preview-aborted", reason="dock-item-missing-or-hidden")
            self._log_preview_hide_reason("pending-preview-item-missing")
            self.hide_preview()
            return

        self.show_preview_for_item(dock_item)

    def _preview_entries_for_item(self, dock_item):
        preview_windows = dock_item.windows or [{'id': dock_item.win_id, 'title': dock_item.app_data.get('Title', '')}]
        previews = []
        for window in preview_windows:
            pixmap = self.capture_window_preview(window.get('id'))
            if pixmap.isNull():
                log_dock_debug(
                    "dock-preview-capture-empty",
                    app_name=dock_item.app_data.get('Name', 'Unknown App'),
                    win_id=window.get('id'),
                )
                continue
            title = dock_item.app_data.get('Name', 'Unknown App')
            window_title = window.get('title', '').strip() or dock_item.app_data.get('Title', '').strip()
            if window_title and window_title != title:
                title = f"{title}\n{window_title}"
            previews.append({
                'title': title,
                'pixmap': pixmap,
                'win_id': window.get('id'),
                'app_data': dock_item.app_data,
                'app_path': dock_item.app_data.get('path'),
                'is_maximized': self.is_window_maximized(window.get('id')),
            })
        return preview_windows, previews

    def _preview_title_for_window(self, app_data, window) -> str:
        title = app_data.get('Name', 'Unknown App')
        window_title = str(window.get('title') or app_data.get('Title') or '').strip()
        if window_title and window_title != title:
            return f"{title}\n{window_title}"
        return title

    def _running_windows_by_id(self) -> dict[str, dict]:
        running_by_id: dict[str, dict] = {}
        for window in self.get_running_windows():
            normalized_win_id = self._normalize_window_id(window.get('id'))
            if normalized_win_id:
                running_by_id[normalized_win_id] = window
        return running_by_id

    def _sync_preview_entries_from_running_windows(self, running_windows_by_id: dict[str, dict]) -> bool:
        if not self.current_preview_entries:
            return False
        synced_entries = []
        for preview in self.current_preview_entries:
            normalized_win_id = self._normalize_window_id(preview.get('win_id'))
            window = running_windows_by_id.get(normalized_win_id)
            if window is None:
                continue
            updated_preview = dict(preview)
            app_data = updated_preview.get('app_data', {})
            updated_preview['title'] = self._preview_title_for_window(app_data, window)
            updated_preview['is_maximized'] = self.is_window_maximized(updated_preview.get('win_id'))
            synced_entries.append(updated_preview)

        if not synced_entries:
            if self.preview_refresh_grace_attempts > 0:
                self.preview_refresh_grace_attempts -= 1
                QTimer.singleShot(180, self.refresh_preview_after_action)
                return True
            return False

        if synced_entries != self.current_preview_entries:
            anchor_item = self.active_preview_item if isValid(self.active_preview_item) else None
            if anchor_item is None or not anchor_item.isVisible():
                anchor_item = self._find_dock_item_by_app_path(self.active_preview_app_path)
            if anchor_item is not None and isValid(anchor_item) and anchor_item.isVisible():
                self._render_preview_entries(anchor_item, synced_entries)
            else:
                self.current_preview_entries = synced_entries
        return True

    def _render_preview_entries(self, dock_item, previews):
        was_visible = self.preview_popup.isVisible()
        self.current_preview_entries = [dict(preview) for preview in previews]
        self.preview_popup.update_content(self.current_preview_entries)
        preview_size = self.preview_popup.sizeHint()
        self.preview_popup.resize(preview_size)
        screen_geometry = self._preview_screen_geometry_for_item(dock_item)
        anchor_top_left = dock_item.mapToGlobal(QPoint(0, 0))
        popup_x = anchor_top_left.x() + (dock_item.width() - self.preview_popup.width()) // 2
        popup_y = anchor_top_left.y() - self.preview_popup.height() - 12
        if not screen_geometry.isNull():
            popup_x = max(
                screen_geometry.left() + 8,
                min(popup_x, screen_geometry.right() - self.preview_popup.width() - 7),
            )
            min_y = screen_geometry.top() + 8
            if popup_y < min_y:
                popup_y = min(
                    anchor_top_left.y() + dock_item.height() + 12,
                    screen_geometry.bottom() - self.preview_popup.height() - 7,
                )

        self.preview_popup.move(popup_x, popup_y)
        self.preview_popup_fade.stop()
        self.preview_popup.show()
        if was_visible:
            self.preview_popup_opacity.setOpacity(1.0)
        else:
            self.preview_popup_opacity.setOpacity(0.0)
            self.preview_popup_fade.setStartValue(0.0)
            self.preview_popup_fade.setEndValue(1.0)
            self.preview_popup_fade.start()
        self._log_window_state(
            "dock-preview-shown",
            app_name=dock_item.app_data.get('Name', 'Unknown App'),
            preview_count=len(previews),
            preview_size=(self.preview_popup.width(), self.preview_popup.height()),
            host_size=(self.preview_host.width(), self.preview_host.height()),
            popup_pos=(popup_x, popup_y),
        )

    def _remove_preview_entry(self, win_id):
        normalized_win_id = self._normalize_window_id(win_id)
        if not normalized_win_id:
            return
        remaining = [
            dict(preview)
            for preview in self.current_preview_entries
            if self._normalize_window_id(preview.get("win_id")) != normalized_win_id
        ]
        if len(remaining) == len(self.current_preview_entries):
            return
        if not remaining:
            self._log_preview_hide_reason("remove-preview-entry-last-card", win_id=normalized_win_id)
            self.hide_preview()
            return
        anchor_item = self.active_preview_item if isValid(self.active_preview_item) else None
        if anchor_item is None or not anchor_item.isVisible():
            anchor_item = self._find_dock_item_by_app_path(self.active_preview_app_path)
        if anchor_item is None or not isValid(anchor_item) or not anchor_item.isVisible():
            self.current_preview_entries = remaining
            return
        self._render_preview_entries(anchor_item, remaining)
        self.preview_refresh_grace_attempts = max(self.preview_refresh_grace_attempts, 3)

    def show_preview_for_item(self, dock_item, *, preserve_on_empty: bool = False):
        if dock_item is None or not isValid(dock_item):
            self._log_preview_hide_reason(
                "show-preview-invalid-item",
                has_item=dock_item is not None,
                preserve_on_empty=preserve_on_empty,
            )
            if preserve_on_empty and self.current_preview_entries:
                return False
            self.hide_preview()
            return False

        if not dock_item.isVisible():
            self._log_preview_hide_reason(
                "show-preview-item-not-visible",
                app_path=self._dock_item_app_path(dock_item),
                preserve_on_empty=preserve_on_empty,
            )
            if preserve_on_empty and self.current_preview_entries:
                return False
            self.hide_preview()
            return False

        self.set_settings_revealed(False)
        self.pending_preview_item = dock_item
        self.active_preview_item = dock_item
        self.active_preview_app_path = self._dock_item_app_path(dock_item)
        self.active_preview_anchor_rect = QRect(
            dock_item.mapToGlobal(QPoint(0, 0)),
            dock_item.size(),
        )
        preview_windows, previews = self._preview_entries_for_item(dock_item)
        if not previews:
            self._log_window_state(
                "dock-preview-empty",
                app_name=dock_item.app_data.get('Name', 'Unknown App'),
                requested_windows=len(preview_windows),
            )
            if preserve_on_empty and self.current_preview_entries:
                return False
            self._log_preview_hide_reason(
                "show-preview-empty",
                app_name=dock_item.app_data.get('Name', 'Unknown App'),
                requested_windows=len(preview_windows),
                preserve_on_empty=preserve_on_empty,
            )
            self.hide_preview()
            return False

        self.preview_refresh_grace_attempts = 0
        self._render_preview_entries(dock_item, previews)
        return True

    def refresh_active_preview(self):
        if not self._has_active_preview_state():
            return
        preserve_on_empty = bool(self.current_preview_entries)
        running_windows_by_id = self._running_windows_by_id()
        dock_item = self._find_dock_item_by_app_path(self.active_preview_app_path)
        if self.current_preview_entries and not self._sync_preview_entries_from_running_windows(running_windows_by_id):
            if dock_item is None or not dock_item.is_running:
                self._log_preview_hide_reason(
                    "refresh-active-preview-sync-empty-app-gone",
                    app_path=self.active_preview_app_path,
                )
                self.hide_preview()
                return
        if dock_item is None or not dock_item.is_running:
            if not self.current_preview_entries:
                self._log_preview_hide_reason(
                    "refresh-active-preview-app-gone",
                    app_path=self.active_preview_app_path,
                )
                self.hide_preview()
            return
        if not self.show_preview_for_item(
            dock_item,
            preserve_on_empty=preserve_on_empty,
        ) and self.preview_refresh_grace_attempts > 0 and self.current_preview_entries:
            self.preview_refresh_grace_attempts -= 1
            QTimer.singleShot(180, self.refresh_preview_after_action)

    def hide_preview(self):
        caller_frame = inspect.currentframe().f_back
        caller_name = caller_frame.f_code.co_name if caller_frame is not None else "unknown"
        caller_line = caller_frame.f_lineno if caller_frame is not None else -1
        log_dock_debug(
            "dock-preview-hide-called",
            caller=caller_name,
            line=caller_line,
            active_preview_app_path=self.active_preview_app_path,
            preview_count=len(self.current_preview_entries),
            popup_visible=self.preview_popup.isVisible(),
            dock_visible=self.isVisible(),
        )
        self._suppress_preview_restore = True
        self.pending_preview_item = None
        self.active_preview_item = None
        self.active_preview_app_path = ""
        self.active_preview_anchor_rect = None
        self.current_preview_entries = []
        self.preview_refresh_grace_attempts = 0
        self.preview_visibility_guard_deadline = 0.0
        self.preview_item_activation_guard_deadline = 0.0
        self.preview_hover_active = False
        self.preview_timer.stop()
        self.preview_hide_timer.stop()
        self.preview_popup_fade.stop()
        self.preview_popup_opacity.setOpacity(1.0)
        self.preview_popup.hide()
        self.preview_host.setFixedSize(0, 0)
        self.adjustSize()
        self.recenter()
        self._suppress_preview_restore = False
        self._log_window_state("dock-preview-hidden")

    def hide_preview_if_inactive(self):
        cursor_pos = QCursor.pos()
        if self.preview_hover_active:
            return
        if self.active_preview_anchor_rect is not None:
            item_rect = QRect(self.active_preview_anchor_rect).adjusted(-10, -10, 10, 10)
            if item_rect.contains(cursor_pos):
                return
        if self.preview_popup.isVisible():
            popup_rect = QRect(
                self.preview_popup.mapToGlobal(QPoint(0, 0)),
                self.preview_popup.size(),
            ).adjusted(-10, -10, 10, 10)
            if popup_rect.contains(cursor_pos):
                return
        self._log_preview_hide_reason("hide-preview-if-inactive")
        self.hide_preview()

    def handle_preview_hover_changed(self, is_hovered):
        self.preview_hover_active = is_hovered
        if is_hovered:
            self.preview_hide_timer.stop()
        elif self.preview_popup.isVisible():
            self.schedule_preview_hide()

    def handle_preview_interaction_started(self):
        self._arm_preview_visibility_guard(0.5)
        self._arm_preview_item_activation_guard(0.5)
        self._last_mouse_buttons = Qt.MouseButton.LeftButton
        cursor_pos = QCursor.pos()
        preview_popup = getattr(self, "preview_popup", None)
        log_dock_debug(
            "dock-preview-interaction-started",
            cursor_pos=(cursor_pos.x(), cursor_pos.y()),
            preview_visibility_guard_deadline=self.preview_visibility_guard_deadline,
            preview_item_activation_guard_deadline=self.preview_item_activation_guard_deadline,
            popup_visible=bool(preview_popup is not None and preview_popup.isVisible()),
            preview_count=len(getattr(self, "current_preview_entries", [])),
        )

    def focus_window(self, win_id, *, app_path=None):
        if not win_id:
            return
        self._run_window_command(['wmctrl', '-i', '-r', win_id, '-b', 'remove,hidden'])
        xdotool_window_id = self._xdotool_window_id(win_id)
        if xdotool_window_id:
            self._run_window_command(['xdotool', 'windowmap', '--sync', xdotool_window_id])
            self._run_window_command(['xdotool', 'windowactivate', '--sync', xdotool_window_id])
            self._run_window_command(['xdotool', 'windowraise', xdotool_window_id])
        else:
            self._run_window_command(['wmctrl', '-i', '-R', win_id])
        self._remember_window_focus(app_path, win_id)

    def minimize_window(self, win_id):
        if not win_id:
            return
        xdotool_window_id = self._xdotool_window_id(win_id)
        if xdotool_window_id:
            self._run_window_command(['xdotool', 'windowminimize', xdotool_window_id])
            return
        self._run_window_command(['wmctrl', '-i', '-r', win_id, '-b', 'add,hidden'])

    def close_window(self, win_id):
        if not win_id:
            return
        self._run_window_command(['wmctrl', '-i', '-c', win_id])

    def open_new_window(self, app_data):
        launch_app(app_data or {})

    def toggle_window_focus(self, win_id, *, app_path=None):
        if not win_id:
            return
        if self.is_window_minimized(win_id):
            self.focus_window(win_id, app_path=app_path)
            return
        if self._normalize_window_id(win_id) == self._active_window_id():
            self.minimize_window(win_id)
            return
        self.focus_window(win_id, app_path=app_path)

    def execute_window_action(self, action_name, payload):
        win_id = str(payload.get('win_id') or '').strip()
        app_data = payload.get('app_data', {})
        app_path = payload.get('app_path') or app_data.get('path')
        if action_name == 'toggle_focus':
            if win_id:
                self.toggle_window_focus(win_id, app_path=app_path)
            else:
                self.open_new_window(app_data)
        elif action_name == 'focus':
            self.focus_window(win_id, app_path=app_path)
        elif action_name == 'minimize':
            self.minimize_window(win_id)
        elif action_name == 'toggle_maximize':
            is_maximized = payload.get('is_maximized')
            if is_maximized is None:
                is_maximized = self.is_window_maximized(win_id)
            self.toggle_window_maximize(win_id, bool(is_maximized))
        elif action_name == 'close':
            self.close_window(win_id)
        elif action_name == 'new_window':
            self.open_new_window(app_data)
        log_dock_debug("dock-window-action", action=action_name, win_id=win_id, app_path=app_path)

    def handle_item_activation(self, dock_item):
        if dock_item is None or not isValid(dock_item):
            return
        if self._preview_item_activation_guard_active():
            return
        if not dock_item.is_running:
            self._log_preview_hide_reason(
                "handle-item-activation-non-running",
                app_path=self._dock_item_app_path(dock_item),
            )
            self.hide_preview()
            self.execute_window_action('new_window', self._item_action_payload(dock_item))
            return
        next_app_path = self._dock_item_app_path(dock_item)
        if self.preview_popup.isVisible() and next_app_path == self.active_preview_app_path:
            self._log_preview_hide_reason("handle-item-activation-toggle-same-app", app_path=next_app_path)
            self.hide_preview()
            return
        if self.preview_popup.isVisible() and next_app_path != self.active_preview_app_path:
            self._log_preview_hide_reason(
                "handle-item-activation-switch-app",
                from_app_path=self.active_preview_app_path,
                to_app_path=next_app_path,
            )
            self.hide_preview()
        self.show_preview_for_item(dock_item)

    def show_item_context_menu(self, dock_item, global_pos):
        self._log_preview_hide_reason(
            "show-item-context-menu",
            app_path=self._dock_item_app_path(dock_item),
        )
        self.hide_preview()
        menu = QMenu(self)
        payload = self._item_action_payload(dock_item)
        win_id = str(payload.get('win_id') or '').strip()
        is_running = bool(dock_item.is_running and win_id)

        if is_running:
            focus_action = QAction("Focus Window", self)
            focus_action.triggered.connect(lambda: self.execute_window_action('focus', dict(payload)))
            menu.addAction(focus_action)

            minimize_action = QAction("Minimize Window", self)
            minimize_action.triggered.connect(lambda: self.execute_window_action('minimize', dict(payload)))
            menu.addAction(minimize_action)

            maximize_text = "Restore Window" if payload.get('is_maximized') else "Maximize Window"
            maximize_action = QAction(maximize_text, self)
            maximize_action.triggered.connect(lambda: self.execute_window_action('toggle_maximize', dict(payload)))
            menu.addAction(maximize_action)

            close_action = QAction("Close Window", self)
            close_action.triggered.connect(lambda: self.execute_window_action('close', dict(payload)))
            menu.addAction(close_action)
            menu.addSeparator()

        new_window_action = QAction("Open New Window", self)
        new_window_action.triggered.connect(lambda: self.execute_window_action('new_window', dict(payload)))
        menu.addAction(new_window_action)

        if not dock_item.app_data.get('runtime_only'):
            menu.addSeparator()
            pin_text = "Unpin from Dock" if dock_item.is_pinned else "Pin to Dock"
            pin_action = QAction(pin_text, self)
            pin_action.triggered.connect(dock_item.toggle_pin)
            menu.addAction(pin_action)

        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(40, 40, 40, 240);
                color: white;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 40);
            }
        """)
        menu.exec(global_pos)

    def handle_preview_action(self, action_name, preview):
        self.handle_preview_interaction_started()
        if action_name == 'close':
            self._remove_preview_entry(preview.get('win_id'))
        self.execute_window_action(action_name, preview)
        QTimer.singleShot(180, self.refresh_preview_after_action)

    def refresh_preview_after_action(self):
        self.update_dock_items()
        if self._has_active_preview_state():
            self.refresh_active_preview()

    def toggle_window_maximize(self, win_id, is_maximized):
        action = 'remove' if is_maximized else 'add'
        self._run_window_command(['wmctrl', '-i', '-r', win_id, '-b', f'{action},maximized_vert,maximized_horz'])

    def is_window_maximized(self, win_id):
        if not win_id:
            return False
        try:
            output = subprocess.check_output(
                ['xprop', '-id', str(win_id), '_NET_WM_STATE'],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return False
        text = output.lower()
        return '_net_wm_state_maximized_vert' in text and '_net_wm_state_maximized_horz' in text

    def is_window_minimized(self, win_id):
        if not win_id:
            return False
        try:
            output = subprocess.check_output(
                ['xprop', '-id', str(win_id), 'WM_STATE', '_NET_WM_STATE'],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return False
        text = output.lower()
        return 'iconic' in text or '_net_wm_state_hidden' in text

    def capture_window_preview(self, win_id):
        pixmap = self.x11_preview_capturer.capture(win_id)
        if not pixmap.isNull():
            log_dock_debug(
                "dock-preview-captured",
                win_id=win_id,
                size=(pixmap.width(), pixmap.height()),
                backend=self.x11_preview_capturer.backend_name,
            )
            return pixmap

        try:
            native_id = int(str(win_id), 0)
        except (TypeError, ValueError):
            try:
                native_id = int(str(win_id), 16)
            except (TypeError, ValueError):
                log_dock_debug("dock-preview-invalid-win-id", win_id=win_id)
                return QPixmap()

        for screen in QApplication.screens():
            pixmap = screen.grabWindow(native_id)
            if not pixmap.isNull():
                log_dock_debug(
                    "dock-preview-captured",
                    win_id=win_id,
                    size=(pixmap.width(), pixmap.height()),
                    screen_geometry=screen.geometry().getRect(),
                    backend="qscreen",
                )
                return pixmap
        log_dock_debug("dock-preview-capture-failed", win_id=win_id)
        return QPixmap()

    def check_mouse_proximity(self):
        pos = QCursor.pos()
        screen_geometry = self._screen_geometry(prefer_cursor=True)
        if screen_geometry.isNull():
            return
        if self._has_active_preview_state() and not self.preview_popup.isVisible():
            self._restore_preview_visibility_if_guarded()
        if self._has_active_preview_state() and self._preview_item_activation_guard_active():
            preview_popup = getattr(self, "preview_popup", None)
            log_dock_debug(
                "dock-preview-outside-click-suppressed",
                cursor_pos=(pos.x(), pos.y()),
                preview_visibility_guard_deadline=getattr(self, "preview_visibility_guard_deadline", 0.0),
                preview_item_activation_guard_deadline=getattr(self, "preview_item_activation_guard_deadline", 0.0),
                popup_visible=bool(preview_popup is not None and preview_popup.isVisible()),
                preview_count=len(getattr(self, "current_preview_entries", [])),
            )
        if (
            self._has_active_preview_state()
            and not self._preview_item_activation_guard_active()
            and self._pointer_pressed_outside_dock(pos)
        ):
            self._log_preview_hide_reason("check-mouse-proximity-outside-click")
            self.hide_preview()
        trigger_area = screen_geometry.bottom() - 4

        if self._has_active_preview_state():
            if not self.is_visible:
                self.show_dock()
            self.update_settings_reveal(pos)
            return

        if screen_geometry.contains(pos) and pos.y() >= trigger_area:
            if not self.is_visible:
                self.show_dock()
        else:
            buffer = 60
            dock_rect = self.geometry()
            buffered_rect = dock_rect.adjusted(-buffer, -buffer, buffer, buffer)
            if not buffered_rect.contains(pos) and self.is_visible:
                log_dock_debug(
                    "dock-auto-hide-buffer-exit",
                    cursor_pos=(pos.x(), pos.y()),
                    buffered_rect=buffered_rect.getRect(),
                    dock_rect=dock_rect.getRect(),
                    has_active_preview=self._has_active_preview_state(),
                    popup_visible=self.preview_popup.isVisible(),
                )
                self.hide_dock()

        self.update_settings_reveal(pos)

    def update_settings_reveal(self, pos):
        if not self.is_visible:
            self.set_settings_revealed(False)
            return

        dock_rect = QRect(self.container.mapToGlobal(QPoint(0, 0)), self.container.size())
        hidden_inside_padding = max(10, int(self.settings_button.width() * 0.25))
        right_padding = self.settings_button.width() + 44 if self.settings_revealed else hidden_inside_padding
        left_padding = 32 if self.settings_revealed else 24
        near_left_edge = (
            dock_rect.left() - left_padding <= pos.x() <= dock_rect.left() + right_padding
            and dock_rect.top() - 18 <= pos.y() <= dock_rect.bottom() + 18
        )
        self.set_settings_revealed(near_left_edge)

    def show_dock(self):
        if self.is_visible:
            return
        self._stop_animation()
        self.is_visible = True

        screen_geometry = self._screen_geometry(prefer_cursor=True)
        target_x = screen_geometry.x() + (screen_geometry.width() - self.width()) // 2
        target_y = self._visible_y(screen_geometry)
        animation_mode = self._visibility_animation_mode()
        was_visible = self.isVisible()

        if animation_mode == "slide":
            start_pos = QPoint(target_x, self._hidden_y(screen_geometry))
            end_pos = QPoint(target_x, target_y)
            if not was_visible:
                self.move(start_pos)
                self.setWindowOpacity(1.0)
                self.show()
                self.move(start_pos)
            else:
                self.move(target_x, self.y())
            self.setWindowOpacity(1.0)
            self.anim = QPropertyAnimation(self, b"pos", self)
            self.anim.setDuration(320)
            self.anim.setStartValue(start_pos if not was_visible else self.pos())
            self.anim.setEndValue(end_pos)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
        else:
            if not was_visible:
                self.move(target_x, target_y)
                self.setWindowOpacity(0.0)
                self.show()
            else:
                self.move(target_x, target_y)
            self.anim = QPropertyAnimation(self, b"windowOpacity", self)
            self.anim.setDuration(280)
            self.anim.setStartValue(self.windowOpacity())
            self.anim.setEndValue(1.0)
            self.anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.x11_window_manager.sync(
            reserve_space=True,
            window_rect=QRect(target_x, target_y, self.width(), self.height()),
        )
        self.anim.finished.connect(self._handle_visibility_animation_finished)
        self.anim.start()
        self._log_window_state(
            "dock-show-animation-started",
            screen_geometry=screen_geometry.getRect(),
            animation_mode=animation_mode,
            target_x=target_x,
            target_y=target_y,
        )

    def hide_dock(self):
        if not self.is_visible:
            return
        caller_frame = inspect.currentframe().f_back
        caller_name = caller_frame.f_code.co_name if caller_frame is not None else "unknown"
        caller_line = caller_frame.f_lineno if caller_frame is not None else -1
        log_dock_debug(
            "dock-hide-called",
            caller=caller_name,
            line=caller_line,
            active_preview_app_path=self.active_preview_app_path,
            preview_count=len(self.current_preview_entries),
            popup_visible=self.preview_popup.isVisible(),
            dock_visible=self.isVisible(),
        )
        self._stop_animation()
        self._log_preview_hide_reason("hide-dock")
        self.hide_preview()
        self.set_settings_revealed(False)
        self.is_visible = False

        screen_geometry = self._screen_geometry()
        target_y = self._hidden_y(screen_geometry)
        animation_mode = self._visibility_animation_mode()

        if animation_mode == "slide":
            self.setWindowOpacity(1.0)
            self.anim = QPropertyAnimation(self, b"pos", self)
            self.anim.setDuration(300)
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(QPoint(self.x(), target_y))
            self.anim.setEasingCurve(QEasingCurve.InCubic)
        else:
            self.anim = QPropertyAnimation(self, b"windowOpacity", self)
            self.anim.setDuration(240)
            self.anim.setStartValue(self.windowOpacity())
            self.anim.setEndValue(0.0)
            self.anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.x11_window_manager.sync(reserve_space=False)
        self.anim.finished.connect(self._handle_visibility_animation_finished)
        self.anim.start()
        self._log_window_state(
            "dock-hide-animation-started",
            screen_geometry=screen_geometry.getRect(),
            animation_mode=animation_mode,
            target_y=target_y,
        )

    def recenter(self):
        screen_geometry = self._screen_geometry(prefer_cursor=not self.is_visible)
        new_x = screen_geometry.x() + (screen_geometry.width() - self.width()) // 2
        if self.is_visible:
            new_y = self._visible_y(screen_geometry)
        elif self.isVisible():
            new_y = self.y()
        else:
            new_y = self._hidden_y(screen_geometry)
        self.move(new_x, new_y)
        self.x11_window_manager.sync(
            reserve_space=self.is_visible,
            window_rect=QRect(new_x, new_y, self.width(), self.height()),
        )
        self._log_window_state(
            "dock-recentered",
            screen_geometry=screen_geometry.getRect(),
            target_pos=(new_x, new_y),
            animation_running=bool(self.anim and self.anim.state()),
        )
