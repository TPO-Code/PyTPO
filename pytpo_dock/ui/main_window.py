from __future__ import annotations

import json
import os
import subprocess

from PySide6.QtCore import QEasingCurve, QAbstractAnimation, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QToolButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from ..apps import build_app_registry, build_runtime_window_app, launch_app, parse_desktop_file
from ..debug import log_dock_debug
from ..settings_dialog import DockSettingsDialog, load_dock_settings
from ..storage_paths import dock_config_dir, dock_pinned_apps_path, migrate_legacy_dock_storage
from .widgets import DockContainerFrame, DockItem, WindowPreview, build_settings_icon


class CustomDock(QWidget):
    def __init__(self):
        super().__init__()
        migrate_legacy_dock_storage()

        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)

        self.registry = build_app_registry()
        self.load_pinned_apps()
        self.dock_settings = load_dock_settings()
        self.anim = None

        self.is_visible = False
        self.last_dock_state = []
        self.pending_preview_item = None
        self.preview_host = QWidget(self)
        self.preview_host.setFixedSize(0, 0)
        self.preview_popup = WindowPreview(self.preview_host)
        self.preview_popup.hide()
        self.preview_popup.hover_changed.connect(self.handle_preview_hover_changed)
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
        self.active_preview_anchor_rect = None

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.container = DockContainerFrame(self)
        self.container.setObjectName("DockContainer")
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

        self.mouse_timer = QTimer(self)
        self.mouse_timer.timeout.connect(self.check_mouse_proximity)
        self.mouse_timer.start(100)

        self.wm_timer = QTimer(self)
        self.wm_timer.timeout.connect(self.update_dock_items)
        self.wm_timer.start(1000)

        self.apply_dock_settings()
        self.update_dock_items()
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

    def _screen_geometry(self):
        screen = QApplication.primaryScreen()
        return screen.geometry() if screen is not None else QRect()

    def _visible_y(self, screen_geometry):
        return screen_geometry.height() - self.height() - 15

    def _hidden_y(self, screen_geometry):
        return screen_geometry.height()

    def _normalize_window_id(self, win_id):
        try:
            return format(int(str(win_id), 0), "x")
        except (TypeError, ValueError):
            return ""

    def _is_own_window(self, win_id):
        own_id = self.winId()
        if not own_id:
            return False
        return self._normalize_window_id(win_id) == self._normalize_window_id(own_id)

    def _stop_animation(self):
        if self.anim is not None and self.anim.state() == QAbstractAnimation.Running:
            self.anim.stop()

    def _handle_visibility_animation_finished(self):
        if self.is_visible:
            self.setWindowOpacity(1.0)
            self._log_window_state("dock-show-animation-finished")
            return

        screen_geometry = self._screen_geometry()
        self.hide()
        self.setWindowOpacity(1.0)
        self.move(self.x(), self._hidden_y(screen_geometry))
        self._log_window_state("dock-hide-animation-finished")

    def showEvent(self, event):
        super().showEvent(event)
        self._log_window_state("dock-show-event")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._log_window_state(
            "dock-resize-event",
            old_size=(event.oldSize().width(), event.oldSize().height()),
            new_size=(event.size().width(), event.size().height()),
        )

    def moveEvent(self, event):
        super().moveEvent(event)
        self._log_window_state(
            "dock-move-event",
            old_pos=(event.oldPos().x(), event.oldPos().y()),
            new_pos=(event.pos().x(), event.pos().y()),
        )

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
        if should_pin and desktop_path not in self.pinned_apps:
            self.pinned_apps.append(desktop_path)
        elif not should_pin and desktop_path in self.pinned_apps:
            self.pinned_apps.remove(desktop_path)
        self.save_pinned_apps()
        self.update_dock_items()

    def get_running_windows(self):
        try:
            output = subprocess.check_output(['wmctrl', '-l', '-x'], text=True)
        except FileNotFoundError:
            print("Warning: 'wmctrl' not found. Cannot track running apps.")
            log_dock_debug("dock-wmctrl-missing")
            return []
        except Exception as exc:
            log_dock_debug("dock-wmctrl-error", error=repr(exc))
            return []

        windows = []
        for line in output.splitlines():
            parts = line.split(maxsplit=4)
            if len(parts) < 5:
                continue
            win_id = parts[0]
            desktop_id = parts[1]
            if desktop_id == '-1':
                continue
            if self._is_own_window(win_id):
                log_dock_debug("dock-own-window-skipped", win_id=win_id)
                continue
            windows.append({
                'id': win_id,
                'class': parts[2].split('.')[-1].lower(),
                'title': parts[4].strip(),
            })
        return windows

    def update_dock_items(self):
        running_windows = self.get_running_windows()
        target_items = []
        added_paths = set()

        for path in self.pinned_apps:
            app_data = parse_desktop_file(path) if os.path.exists(path) else None
            if not app_data:
                continue

            wm_class = app_data.get('StartupWMClass', '').lower()
            if not wm_class:
                wm_class = os.path.basename(path).lower().replace('.desktop', '')

            matching_windows = [window for window in running_windows if window['class'] == wm_class or wm_class in window['class']]
            target_items.append({
                'path': path,
                'data': app_data,
                'is_pinned': True,
                'is_running': bool(matching_windows),
                'win_id': matching_windows[0]['id'] if matching_windows else None,
                'windows': matching_windows,
            })
            added_paths.add(path)

        for window in running_windows:
            wm_class = window['class']
            if wm_class in self.registry:
                app_data = self.registry[wm_class]
                path = app_data['path']
                if path in added_paths:
                    continue
                matching_windows = [item for item in running_windows if item['class'] == wm_class]
                target_items.append({
                    'path': path,
                    'data': app_data,
                    'is_pinned': False,
                    'is_running': True,
                    'win_id': matching_windows[0]['id'],
                    'windows': matching_windows,
                })
                added_paths.add(path)
                continue

            path = f"window://{window['id']}"
            if path in added_paths:
                continue
            target_items.append({
                'path': path,
                'data': build_runtime_window_app(window),
                'is_pinned': False,
                'is_running': True,
                'win_id': window['id'],
                'windows': [window],
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

    def rebuild_layout(self, items):
        if self.preview_popup.isVisible() or self.pending_preview_item is not None:
            self.hide_preview()

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
                indicator_mode=self.dock_settings.instance_indicator_mode,
            )
            button.pin_toggled.connect(self.handle_pin_toggle)
            button.preview_requested.connect(self.schedule_preview)
            button.preview_hidden.connect(self.schedule_preview_hide)
            self.app_row_layout.addWidget(button)

        self.adjustSize()
        self.recenter()
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
        settings_action.triggered.connect(self.open_settings_dialog)
        menu.addAction(settings_action)
        menu.addSeparator()

        open_config_action = QAction("Open Dock Config Folder", self)
        open_config_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(dock_config_dir())))
        )
        menu.addAction(open_config_action)

        reload_action = QAction("Reload Applications", self)
        reload_action.triggered.connect(self.reload_registry)
        menu.addAction(reload_action)

        menu.addSeparator()

        quit_action = QAction("Quit Dock", self)
        quit_action.triggered.connect(QApplication.instance().quit)
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
        menu.exec(self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft()))

    def open_settings_dialog(self):
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
        self.container_layout.setContentsMargins(padding, padding, padding, padding)
        self.container_layout.setSpacing(spacing)
        self.app_row_layout.setSpacing(spacing)
        self.settings_button.setFixedSize(button_size, button_size)
        self.settings_button.setIconSize(QSize(max(18, button_size - 20), max(18, button_size - 20)))
        self.settings_panel.setMaximumWidth(self.settings_button.width() if self.settings_revealed else 0)

        self.update_dock_items()
        self.adjustSize()
        self.recenter()
        self._log_window_state(
            "dock-settings-applied",
            padding=padding,
            spacing=spacing,
            button_size=button_size,
            icon_size=self.dock_settings.icon_size,
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
        self.preview_timer.start(200)
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
            self.hide_preview()
            return

        self.active_preview_anchor_rect = QRect(
            dock_item.mapToGlobal(QPoint(0, 0)),
            dock_item.size(),
        )
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
                'is_maximized': self.is_window_maximized(window.get('id')),
            })

        if not previews:
            self._log_window_state(
                "dock-preview-empty",
                app_name=dock_item.app_data.get('Name', 'Unknown App'),
                requested_windows=len(preview_windows),
            )
            return

        self.preview_popup.update_content(previews)
        preview_size = self.preview_popup.sizeHint()
        self.preview_popup.resize(preview_size)
        host_width = max(self.container.sizeHint().width(), preview_size.width() + 16)
        self.preview_host.setFixedSize(host_width, preview_size.height() + 12)
        self.adjustSize()
        self.recenter()

        button_top_left = self.preview_host.mapFromGlobal(dock_item.mapToGlobal(QPoint(0, 0)))
        popup_x = button_top_left.x() + (dock_item.width() - self.preview_popup.width()) // 2
        popup_x = max(8, min(popup_x, self.preview_host.width() - self.preview_popup.width() - 8))
        popup_y = max(0, self.preview_host.height() - self.preview_popup.height() - 4)

        self.preview_popup.move(popup_x, popup_y)
        self.preview_popup.show()
        self._log_window_state(
            "dock-preview-shown",
            app_name=dock_item.app_data.get('Name', 'Unknown App'),
            preview_count=len(previews),
            preview_size=(self.preview_popup.width(), self.preview_popup.height()),
            host_size=(self.preview_host.width(), self.preview_host.height()),
            popup_pos=(popup_x, popup_y),
        )

    def hide_preview(self):
        self.pending_preview_item = None
        self.active_preview_item = None
        self.active_preview_anchor_rect = None
        self.preview_hover_active = False
        self.preview_timer.stop()
        self.preview_hide_timer.stop()
        self.preview_popup.hide()
        self.preview_host.setFixedSize(0, 0)
        self.adjustSize()
        self.recenter()
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
        self.hide_preview()

    def handle_preview_hover_changed(self, is_hovered):
        self.preview_hover_active = is_hovered
        if is_hovered:
            self.preview_hide_timer.stop()
        elif self.preview_popup.isVisible():
            self.schedule_preview_hide()

    def handle_preview_action(self, action_name, preview):
        win_id = str(preview.get('win_id') or '').strip()
        if action_name == 'focus' and win_id:
            subprocess.run(['wmctrl', '-i', '-a', win_id])
        elif action_name == 'minimize' and win_id:
            subprocess.run(['wmctrl', '-i', '-r', win_id, '-b', 'add,hidden'])
        elif action_name == 'toggle_maximize' and win_id:
            self.toggle_window_maximize(win_id, bool(preview.get('is_maximized')))
        elif action_name == 'close' and win_id:
            subprocess.run(['wmctrl', '-i', '-c', win_id])
        elif action_name == 'new_window':
            launch_app(preview.get('app_data', {}))

        self.hide_preview()
        QTimer.singleShot(180, self.update_dock_items)

    def toggle_window_maximize(self, win_id, is_maximized):
        action = 'remove' if is_maximized else 'add'
        subprocess.run(['wmctrl', '-i', '-r', win_id, '-b', f'{action},maximized_vert,maximized_horz'])

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

    def capture_window_preview(self, win_id):
        try:
            native_id = int(win_id, 16)
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
                )
                return pixmap
        log_dock_debug("dock-preview-capture-failed", win_id=win_id)
        return QPixmap()

    def check_mouse_proximity(self):
        pos = QCursor.pos()
        screen_geometry = QApplication.primaryScreen().geometry()
        trigger_area = screen_geometry.height() - 5

        if pos.y() >= trigger_area:
            if not self.is_visible:
                self.show_dock()
        else:
            buffer = 60
            dock_rect = self.geometry()
            buffered_rect = dock_rect.adjusted(-buffer, -buffer, buffer, buffer)
            if not buffered_rect.contains(pos) and self.is_visible:
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

        screen_geometry = self._screen_geometry()
        target_y = self._visible_y(screen_geometry)
        if not self.isVisible():
            self.move(self.x(), target_y)
            self.setWindowOpacity(0.0)
            self.show()

        self.anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.anim.setDuration(180)
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(self._handle_visibility_animation_finished)
        self.anim.start()
        self._log_window_state(
            "dock-show-animation-started",
            screen_geometry=screen_geometry.getRect(),
            target_y=target_y,
        )

    def hide_dock(self):
        if not self.is_visible:
            return
        self._stop_animation()
        self.hide_preview()
        self.set_settings_revealed(False)
        self.is_visible = False

        screen_geometry = self._screen_geometry()
        target_y = self._hidden_y(screen_geometry)

        self.anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.anim.setDuration(180)
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.InCubic)
        self.anim.finished.connect(self._handle_visibility_animation_finished)
        self.anim.start()
        self._log_window_state(
            "dock-hide-animation-started",
            screen_geometry=screen_geometry.getRect(),
            target_y=target_y,
        )

    def recenter(self):
        screen_geometry = self._screen_geometry()
        new_x = (screen_geometry.width() - self.width()) // 2
        if self.is_visible:
            new_y = self._visible_y(screen_geometry)
        elif self.isVisible():
            new_y = self.y()
        else:
            new_y = self._hidden_y(screen_geometry)
        self.move(new_x, new_y)
        self._log_window_state(
            "dock-recentered",
            screen_geometry=screen_geometry.getRect(),
            target_pos=(new_x, new_y),
            animation_running=bool(self.anim and self.anim.state()),
        )
