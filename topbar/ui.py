from __future__ import annotations

import logging
import time

from PySide6.QtCore import (
    QEasingCurve,
    QDate,
    QDateTime,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import QColor, QCursor, QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from topbar.calendar_popup import CalendarPopup

from .appearance import TopBarPanel, apply_color_opacity, color_from_setting, color_to_qss_rgba
from .constants import NOTIFICATIONS_SERVICE, WATCHER_SERVICES
from .dbus import launch_background_command, load_xlib
from .focus import X11FocusController
from .notifications import NotificationCenter, NotificationCenterButton, NotificationServer
from .settings import TopBarBehaviorSettings, load_topbar_behavior_settings
from .settings_dialog import TopBarSettingsDialog
from .system_menu import SystemMenuButton
from .tray import StatusNotifierTrayArea, StatusNotifierWatcher, X11TraySelectionManager
from .x11_topbar_window import build_top_strut_reservation

LOGGER = logging.getLogger("topbar.ui")

_AUTO_HIDE_POLL_INTERVAL_MS = 40
_AUTO_HIDE_MIN_WIDTH = 360


class TopBar(QWidget):
    def __init__(self):
        super().__init__()
        startup_started = time.perf_counter()
        self._x11_panel_hints_applied = False
        self._behavior_settings = TopBarBehaviorSettings()
        self._auto_hide_enabled = False
        self._is_hidden_to_edge = False
        self._visible_reserve_height = 0
        self._visibility_animation: QParallelAnimationGroup | None = None
        dock_attribute = getattr(Qt.WidgetAttribute, "WA_X11NetWmWindowTypeDock", None)
        if dock_attribute is not None:
            self.setAttribute(dock_attribute, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        center_started = time.perf_counter()
        self.notification_center = NotificationCenter(self)
        LOGGER.info("startup timing: NotificationCenter initialized in %.1f ms", (time.perf_counter() - center_started) * 1000.0)

        server_started = time.perf_counter()
        self.notification_server = NotificationServer(self.notification_center, self)
        LOGGER.info("startup timing: NotificationServer initialized in %.1f ms", (time.perf_counter() - server_started) * 1000.0)

        watcher_started = time.perf_counter()
        self.status_notifier_watcher = StatusNotifierWatcher(self)
        LOGGER.info(
            "startup timing: StatusNotifierWatcher initialized in %.1f ms",
            (time.perf_counter() - watcher_started) * 1000.0,
        )

        tray_selection_started = time.perf_counter()
        self.x11_tray_selection_manager = X11TraySelectionManager(self, self)
        LOGGER.info(
            "startup timing: X11TraySelectionManager initialized in %.1f ms",
            (time.perf_counter() - tray_selection_started) * 1000.0,
        )

        screen = QApplication.primaryScreen()
        width = screen.geometry().width() if screen else 1200
        self.setGeometry(0, 0, width, 35)
        self.focus_controller = X11FocusController(self)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._panel = TopBarPanel(self)
        self._root_layout.addWidget(self._panel)

        self._panel_layout = QHBoxLayout(self._panel)
        self._panel_layout.setContentsMargins(15, 0, 15, 0)
        self._panel_layout.setSpacing(24)

        self._left_section = QWidget(self._panel)
        self._left_section.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._left_layout = QHBoxLayout(self._left_section)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(8)

        self._center_section = QWidget(self._panel)
        self._center_section.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._center_layout = QHBoxLayout(self._center_section)
        self._center_layout.setContentsMargins(0, 0, 0, 0)
        self._center_layout.setSpacing(8)
        self._center_section.hide()

        self._right_section = QWidget(self._panel)
        self._right_section.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._right_layout = QHBoxLayout(self._right_section)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setSpacing(8)

        self._panel_layout.addWidget(self._left_section, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self._panel_layout.addStretch(1)
        self._panel_layout.addWidget(self._center_section, 0, Qt.AlignCenter)
        self._panel_layout.addStretch(1)
        self._panel_layout.addWidget(self._right_section, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.workspaces_label = QLabel("Workspaces: 1 2 3")
        self.workspaces_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._left_layout.addWidget(self.workspaces_label, alignment=Qt.AlignLeft | Qt.AlignVCenter)

        tray_area_started = time.perf_counter()
        self.tray_area = StatusNotifierTrayArea(
            self.status_notifier_watcher,
            self.x11_tray_selection_manager,
            self.focus_controller,
            self._right_section,
        )
        LOGGER.info("startup timing: StatusNotifierTrayArea initialized in %.1f ms", (time.perf_counter() - tray_area_started) * 1000.0)
        self._right_layout.addWidget(self.tray_area, alignment=Qt.AlignRight | Qt.AlignVCenter)

        notifications_button_started = time.perf_counter()
        self.notifications_button = NotificationCenterButton(self.notification_center, self.notification_server, self._right_section)
        LOGGER.info(
            "startup timing: NotificationCenterButton initialized in %.1f ms",
            (time.perf_counter() - notifications_button_started) * 1000.0,
        )
        self._right_layout.addWidget(self.notifications_button, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self.menu_button = SystemMenuButton(
            open_terminal=self._open_terminal,
            open_dock=self._open_dock_panel,
            open_settings=self._open_settings_dialog,
            focus_controller=self.focus_controller,
            parent=self._right_section,
        )
        self._right_layout.addWidget(self.menu_button, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self.clock_btn = QPushButton()
        self.clock_btn.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self._right_layout.addWidget(self.clock_btn, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_runtime_status)
        self._status_timer.start(3000)

        self._auto_hide_poll_timer = QTimer(self)
        self._auto_hide_poll_timer.setInterval(_AUTO_HIDE_POLL_INTERVAL_MS)
        self._auto_hide_poll_timer.timeout.connect(self._poll_auto_hide)
        self._auto_hide_reveal_timer = QTimer(self)
        self._auto_hide_reveal_timer.setSingleShot(True)
        self._auto_hide_reveal_timer.timeout.connect(self._reveal_topbar)
        self._auto_hide_hide_timer = QTimer(self)
        self._auto_hide_hide_timer.setSingleShot(True)
        self._auto_hide_hide_timer.timeout.connect(self._hide_topbar_to_edge)

        self.clock_btn.installEventFilter(self)
        self.clock_btn.clicked.connect(self.show_calendar)

        self.calendar_popup = CalendarPopup(self)
        self.calendar_popup.calendar.clicked.connect(self.handle_date_selected)
        self.calendar_popup.popupHidden.connect(self._restore_previous_focus)
        self.update_date_tooltip()

        self.status_notifier_watcher.itemsChanged.connect(self._refresh_runtime_status)
        self.notification_center.notificationsChanged.connect(self._refresh_runtime_status)

        self._last_status_text = ""
        self._settings_dialog: TopBarSettingsDialog | None = None
        self._refresh_runtime_status()
        self.reload_behavior_settings()
        QTimer.singleShot(0, self._claim_x11_tray_selection)
        LOGGER.info("startup timing: TopBar.__init__ completed in %.1f ms", (time.perf_counter() - startup_started) * 1000.0)

    @Slot()
    def _update_clock(self) -> None:
        time_format = str(self._behavior_settings.appearance_time_format or "").strip() or "h:mm:ss AP"
        self.clock_btn.setText(QDateTime.currentDateTime().toString(time_format))
        self.update_date_tooltip()

    def update_date_tooltip(self) -> None:
        date_format = str(self._behavior_settings.appearance_date_format or "").strip() or "dd/MM/yyyy"
        self.clock_btn.setToolTip(QDate.currentDate().toString(date_format))

    def eventFilter(self, obj, event):
        if obj == self.clock_btn:
            if event.type() == QEvent.Type.Enter:
                self.update_date_tooltip()
                rect = self.clock_btn.rect()
                pos = self.clock_btn.mapToGlobal(rect.center())
                QToolTip.showText(pos, self.clock_btn.toolTip(), self.clock_btn, rect)
            elif event.type() == QEvent.Type.Leave:
                QToolTip.hideText()
        return super().eventFilter(obj, event)

    def handle_date_selected(self, date: QDate) -> None:
        print(f"User picked: {date.toString('dd/MM/yyyy')}")
        self.calendar_popup.hide()

    def show_calendar(self) -> None:
        self._reveal_topbar(immediate=True)
        self.calendar_popup.reset_for_open()

        popup_size = self.calendar_popup.sizeHint()
        button_rect = self.clock_btn.rect()
        global_bottom_right = self.clock_btn.mapToGlobal(button_rect.bottomRight())

        x_pos = global_bottom_right.x() - popup_size.width()
        y_pos = global_bottom_right.y()

        self.calendar_popup.move(x_pos, y_pos)
        self.calendar_popup.show()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._restore_previous_focus()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange:
            self.update_date_tooltip()
        super().changeEvent(event)

    @Slot()
    def _restore_previous_focus(self) -> None:
        self.focus_controller.restore_last_external_window_soon(0)

    def _open_settings_dialog(self) -> None:
        self._reveal_topbar(immediate=True)
        dialog = self._settings_dialog
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return

        dialog = TopBarSettingsDialog(self, on_applied=self.reload_behavior_settings)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(lambda *_args: setattr(self, "_settings_dialog", None))
        self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @Slot()
    def _claim_x11_tray_selection(self) -> None:
        self.x11_tray_selection_manager.claim()
        self.tray_area.sync_items()
        self._refresh_runtime_status()

    def _refresh_runtime_status(self) -> None:
        notify_status = (
            f"Notify: active ({NOTIFICATIONS_SERVICE} at /org/freedesktop/Notifications)"
            if self.notification_server.is_active
            else f"Notify: {self.notification_server.last_error or 'inactive'}"
        )
        watcher_status = (
            f"Watcher: active ({', '.join(self.status_notifier_watcher._service_names or WATCHER_SERVICES)})"
            if self.status_notifier_watcher.is_active
            else f"Watcher: {self.status_notifier_watcher.last_error or 'inactive'}"
        )
        x11_status = (
            f"X11 tray: owner of {self.x11_tray_selection_manager.selection_name or '_NET_SYSTEM_TRAY_S0'}"
            if self.x11_tray_selection_manager.is_owner
            else f"X11 tray: {self.x11_tray_selection_manager.last_error or 'inactive'}"
        )
        status_text = " | ".join((notify_status, watcher_status, x11_status))
        if status_text != self._last_status_text:
            LOGGER.info(status_text)
            self._last_status_text = status_text
        self.setToolTip("")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_x11_panel_hints)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_x11_panel_hints)

    def reload_behavior_settings(self) -> None:
        self.apply_behavior_settings(load_topbar_behavior_settings())

    def _shadow_margins(self) -> tuple[int, int, int, int]:
        if not self._behavior_settings.appearance_show_shadow or int(self._behavior_settings.appearance_shadow_opacity) <= 0:
            return (0, 0, 0, 0)
        blur = max(0, int(self._behavior_settings.appearance_shadow_blur))
        offset_x = int(self._behavior_settings.appearance_shadow_offset_x)
        offset_y = int(self._behavior_settings.appearance_shadow_offset_y)
        return (
            max(0, blur - offset_x),
            max(0, blur - offset_y),
            max(0, blur + offset_x),
            max(0, blur + offset_y),
        )

    def _apply_text_shadow(self, widget: QWidget, enabled: bool) -> None:
        if not enabled:
            effect = widget.graphicsEffect()
            if isinstance(effect, QGraphicsDropShadowEffect):
                widget.setGraphicsEffect(None)
            return
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setBlurRadius(6)
        effect.setOffset(0, 1)
        effect.setColor(QColor(0, 0, 0, 180))

    def _style_for_buttons(self, *, mode: str | None = None) -> str:
        text_color = color_from_setting(self._behavior_settings.appearance_label_text_color, "#f1f1f1")
        background_mode = str(mode or self._behavior_settings.appearance_button_background_style or "subtle").strip().lower()
        border_mode = str(self._behavior_settings.appearance_button_border_style or "soft").strip().lower()
        hover_mode = str(self._behavior_settings.appearance_button_hover_style or "highlight").strip().lower()
        pressed_mode = str(self._behavior_settings.appearance_button_pressed_style or "inset").strip().lower()

        if background_mode == "filled":
            base_bg = apply_color_opacity(text_color, 18)
        elif background_mode == "transparent":
            base_bg = QColor(0, 0, 0, 0)
        else:
            base_bg = apply_color_opacity(text_color, 8)

        if border_mode == "outline":
            border_color = apply_color_opacity(text_color, 34)
        elif border_mode == "none":
            border_color = QColor(0, 0, 0, 0)
        else:
            border_color = apply_color_opacity(text_color, 18)

        if hover_mode == "filled":
            hover_bg = apply_color_opacity(text_color, 24)
        elif hover_mode == "inset":
            hover_bg = apply_color_opacity(text_color, 12)
        elif hover_mode == "none":
            hover_bg = base_bg
        else:
            hover_bg = apply_color_opacity(text_color, 16)

        if pressed_mode == "filled":
            pressed_bg = apply_color_opacity(text_color, 30)
        elif pressed_mode == "highlight":
            pressed_bg = apply_color_opacity(text_color, 22)
        elif pressed_mode == "none":
            pressed_bg = hover_bg
        else:
            pressed_bg = apply_color_opacity(text_color, 12)

        padding = max(0, int(self._behavior_settings.appearance_button_padding))
        radius = max(0, int(self._behavior_settings.appearance_button_corner_radius))
        return (
            "QToolButton, QPushButton {"
            f"color: {color_to_qss_rgba(text_color)};"
            f"background: {color_to_qss_rgba(base_bg)};"
            f"border: 1px solid {color_to_qss_rgba(border_color)};"
            f"border-radius: {radius}px;"
            f"padding: 0 {padding}px;"
            "}"
            "QToolButton:hover, QPushButton:hover {"
            f"background: {color_to_qss_rgba(hover_bg)};"
            "}"
            "QToolButton:pressed, QPushButton:pressed {"
            f"background: {color_to_qss_rgba(pressed_bg)};"
            "}"
        )

    def _apply_appearance_settings(self) -> None:
        settings = self._behavior_settings
        self._panel.apply_settings(settings)

        shadow_left, shadow_top, shadow_right, shadow_bottom = self._shadow_margins()
        self._root_layout.setContentsMargins(
            max(0, int(settings.appearance_left_margin)) + shadow_left,
            max(0, int(settings.appearance_top_margin)) + shadow_top,
            max(0, int(settings.appearance_right_margin)) + shadow_right,
            shadow_bottom,
        )
        self._panel_layout.setContentsMargins(
            max(0, int(settings.appearance_internal_padding)),
            0,
            max(0, int(settings.appearance_internal_padding)),
            0,
        )
        self._panel_layout.setSpacing(max(0, int(settings.appearance_section_spacing)))

        default_spacing = max(0, int(settings.appearance_widget_spacing))
        self._left_layout.setSpacing(max(0, int(settings.appearance_left_section_spacing if settings.appearance_left_section_spacing is not None else default_spacing)))
        self._center_layout.setSpacing(max(0, int(settings.appearance_center_section_spacing if settings.appearance_center_section_spacing is not None else default_spacing)))
        self._right_layout.setSpacing(max(0, int(settings.appearance_right_section_spacing if settings.appearance_right_section_spacing is not None else default_spacing)))

        total_height = (
            self._root_layout.contentsMargins().top()
            + int(settings.appearance_height)
            + self._root_layout.contentsMargins().bottom()
        )
        self.setFixedHeight(max(24, total_height))

        label_font = QFont(self.workspaces_label.font())
        label_family = str(settings.appearance_label_font_family or "").strip()
        if label_family:
            label_font.setFamily(label_family)
        label_font.setPointSize(max(1, int(settings.appearance_label_font_size)))
        label_font.setWeight(QFont.Weight(max(1, min(900, int(settings.appearance_label_font_weight)))))
        self.workspaces_label.setFont(label_font)
        label_color = color_from_setting(settings.appearance_label_text_color, "#f1f1f1")
        self.workspaces_label.setStyleSheet(f"color: {color_to_qss_rgba(label_color)};")
        self._apply_text_shadow(self.workspaces_label, bool(settings.appearance_label_text_shadow))

        clock_font = QFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        clock_family = str(settings.appearance_clock_font_family or "").strip()
        if clock_family:
            clock_font.setFamily(clock_family)
        clock_font.setPointSize(max(1, int(settings.appearance_clock_size)))
        self.clock_btn.setFont(clock_font)
        clock_color = color_from_setting(settings.appearance_clock_color, "#f1f1f1")

        button_style = self._style_for_buttons()
        for button in (self.notifications_button, self.menu_button, self.clock_btn):
            button.setStyleSheet(button_style)
            button.setMinimumHeight(max(20, int(settings.appearance_button_size)))
            button.setMaximumHeight(max(20, int(settings.appearance_button_size)))
        self.clock_btn.setStyleSheet(
            button_style
            + f"QPushButton {{ color: {color_to_qss_rgba(clock_color)}; }}"
        )

        button_icon_size = max(12, int(settings.appearance_button_icon_size))
        self.notifications_button.setIconSize(QSize(button_icon_size, button_icon_size))
        self.menu_button.set_status_icon_size(button_icon_size)

        self.clock_btn.setVisible(bool(settings.appearance_show_clock))
        self.update_date_tooltip()
        self._update_clock()

        tray_style_mode = str(settings.appearance_tray_button_style or "match_buttons").strip().lower()
        if tray_style_mode == "filled":
            tray_button_style = self._style_for_buttons(mode="filled")
        elif tray_style_mode == "transparent":
            tray_button_style = self._style_for_buttons(mode="transparent")
        else:
            tray_button_style = ""
        self.tray_area.apply_appearance(
            icon_size=max(12, int(settings.appearance_tray_icon_size)),
            button_size=max(
                int(settings.appearance_button_size),
                int(settings.appearance_tray_icon_size) + 10,
                30,
            ),
            spacing=max(0, int(settings.appearance_tray_icon_spacing)),
            button_style_sheet=tray_button_style,
        )
        self.menu_button.apply_settings(settings)

        if self.isVisible():
            screen_rect = self._screen_geometry()
            self.setGeometry(screen_rect.x(), self.y(), screen_rect.width(), self.height())
        self.update()

    def apply_behavior_settings(self, settings: TopBarBehaviorSettings | None = None) -> None:
        self._behavior_settings = settings or TopBarBehaviorSettings()
        self._apply_appearance_settings()
        self._auto_hide_enabled = bool(self._behavior_settings.auto_hide)
        self._auto_hide_reveal_timer.stop()
        self._auto_hide_hide_timer.stop()

        if self._auto_hide_enabled:
            self._auto_hide_poll_timer.start()
            self._reveal_topbar(immediate=True)
            self._poll_auto_hide()
            return

        self._auto_hide_poll_timer.stop()
        self._apply_visibility_state(hidden=False, animated=False)

    def _screen_geometry(self) -> QRect:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, max(1, self.width()), max(1, self.height()))
        return screen.geometry()

    def _visible_geometry_for_screen(self, screen_rect: QRect) -> QRect:
        return QRect(screen_rect.x(), screen_rect.y(), screen_rect.width(), self.height())

    def _hidden_width_for_screen(self, screen_rect: QRect) -> int:
        width_percent = max(10, min(100, int(self._behavior_settings.auto_hide_expand_initial_width_percent)))
        return max(_AUTO_HIDE_MIN_WIDTH, int(screen_rect.width() * (width_percent / 100.0)))

    def _expand_origin_x(self, screen_rect: QRect, hidden_width: int) -> int:
        origin = str(self._behavior_settings.auto_hide_expand_origin or "center").strip().lower()
        if origin == "left":
            return screen_rect.x()
        if origin == "right":
            return screen_rect.right() - hidden_width + 1
        return screen_rect.x() + (screen_rect.width() - hidden_width) // 2

    def _hidden_geometry_for_screen(self, screen_rect: QRect) -> QRect:
        width = screen_rect.width()
        x_pos = screen_rect.x()
        if self._behavior_settings.auto_hide_effect_expand_width:
            width = min(screen_rect.width(), self._hidden_width_for_screen(screen_rect))
            x_pos = self._expand_origin_x(screen_rect, width)
        return QRect(x_pos, screen_rect.y() - self.height(), width, self.height())

    def _hidden_animation_geometry_for_screen(self, screen_rect: QRect) -> QRect:
        geometry = self._hidden_geometry_for_screen(screen_rect)
        if not self._behavior_settings.auto_hide_effect_slide:
            geometry.moveTop(screen_rect.y())
        return geometry

    def _animation_easing_curve(self, *, hidden: bool) -> QEasingCurve.Type:
        easing_name = (
            self._behavior_settings.auto_hide_hide_easing
            if hidden
            else self._behavior_settings.auto_hide_show_easing
        )
        normalized = str(easing_name or "").strip().lower()
        if normalized == "ease_in":
            return QEasingCurve.InCubic
        if normalized == "ease_in_out":
            return QEasingCurve.InOutCubic
        if normalized == "linear":
            return QEasingCurve.Linear
        return QEasingCurve.OutCubic

    def _current_reserved_height(self) -> int:
        if self._auto_hide_enabled:
            return 0
        if not self._behavior_settings.reserve_screen_space:
            return 0
        return self.height()

    def _stop_visibility_animation(self) -> None:
        if self._visibility_animation is None:
            return
        try:
            self._visibility_animation.stop()
        except Exception:
            pass
        self._visibility_animation.deleteLater()
        self._visibility_animation = None

    def _apply_visibility_state(self, *, hidden: bool, animated: bool) -> None:
        self._stop_visibility_animation()
        screen_rect = self._screen_geometry()
        visible_geometry = self._visible_geometry_for_screen(screen_rect)
        hidden_rest_geometry = self._hidden_geometry_for_screen(screen_rect)
        hidden_animation_geometry = self._hidden_animation_geometry_for_screen(screen_rect)
        target_geometry = hidden_rest_geometry if hidden else visible_geometry
        use_slide = bool(self._behavior_settings.auto_hide_effect_slide)
        use_fade = bool(self._behavior_settings.auto_hide_effect_fade)
        use_expand = bool(self._behavior_settings.auto_hide_effect_expand_width)
        animate_geometry = use_slide or use_expand
        duration = max(0, int(self._behavior_settings.auto_hide_animation_duration_ms))

        self._is_hidden_to_edge = hidden
        if not hidden and not self.isVisible():
            self.show()
        self._apply_x11_panel_hints()

        if not animated or duration <= 0 or (not animate_geometry and not use_fade):
            self.setGeometry(target_geometry)
            self.setWindowOpacity(0.0 if hidden and use_fade else 1.0)
            return

        animation_target_geometry = hidden_animation_geometry if hidden else visible_geometry
        easing_curve = self._animation_easing_curve(hidden=hidden)
        if not self.isVisible():
            self.show()

        if animate_geometry:
            if hidden:
                self.setGeometry(visible_geometry)
            else:
                self.setGeometry(hidden_animation_geometry)
        else:
            self.setGeometry(visible_geometry)

        group = QParallelAnimationGroup(self)

        if animate_geometry:
            geometry_animation = QPropertyAnimation(self, b"geometry", self)
            geometry_animation.setDuration(duration)
            geometry_animation.setStartValue(self.geometry())
            geometry_animation.setEndValue(animation_target_geometry)
            geometry_animation.setEasingCurve(easing_curve)
            group.addAnimation(geometry_animation)

        if use_fade:
            start_opacity = self.windowOpacity()
            end_opacity = 0.0 if hidden else 1.0
            if not hidden:
                start_opacity = 0.0
                self.setWindowOpacity(0.0)
            opacity_animation = QPropertyAnimation(self, b"windowOpacity", self)
            opacity_animation.setDuration(duration)
            opacity_animation.setStartValue(start_opacity)
            opacity_animation.setEndValue(end_opacity)
            opacity_animation.setEasingCurve(easing_curve)
            group.addAnimation(opacity_animation)
        elif not hidden:
            self.setWindowOpacity(1.0)

        group.finished.connect(lambda: self._finalize_visibility_state(hidden=hidden, geometry=target_geometry, use_fade=use_fade))
        self._visibility_animation = group
        group.start()

    def _finalize_visibility_state(self, *, hidden: bool, geometry: QRect, use_fade: bool) -> None:
        self.setGeometry(geometry)
        if hidden and not use_fade:
            self.setWindowOpacity(1.0)
        elif not hidden:
            self.setWindowOpacity(1.0)
        self._visibility_animation = None

    def _owns_widget(self, widget: QWidget | None) -> bool:
        current = widget
        while current is not None:
            if current is self:
                return True
            current = current.parentWidget()
        return False

    def _has_attached_popup(self) -> bool:
        if self.calendar_popup.isVisible():
            return True
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            return True
        panel = getattr(self.menu_button, "_panel", None)
        if isinstance(panel, QWidget) and panel.isVisible():
            return True
        active_popup = QApplication.activePopupWidget()
        if isinstance(active_popup, QWidget) and self._owns_widget(active_popup):
            return True
        active_modal = QApplication.activeModalWidget()
        if isinstance(active_modal, QWidget) and self._owns_widget(active_modal):
            return True
        return False

    def _pointer_is_in_reveal_area(self, pos: QPoint) -> bool:
        screen_rect = self._screen_geometry()
        reveal_height = max(1, int(self._behavior_settings.auto_hide_reveal_distance_px))
        reveal_rect = QRect(screen_rect.x(), screen_rect.y(), screen_rect.width(), reveal_height)
        return reveal_rect.contains(pos)

    def _pointer_is_over_topbar(self, pos: QPoint) -> bool:
        if self._is_hidden_to_edge:
            return False
        hot_rect = self.frameGeometry().adjusted(0, 0, 0, 4)
        return hot_rect.contains(pos)

    def _poll_auto_hide(self) -> None:
        if not self._auto_hide_enabled:
            return

        pos = QCursor.pos()
        keep_visible = self._pointer_is_over_topbar(pos) or self._has_attached_popup()
        in_reveal_area = self._pointer_is_in_reveal_area(pos)

        if self._is_hidden_to_edge:
            self._auto_hide_hide_timer.stop()
            if in_reveal_area or self._has_attached_popup():
                if not self._auto_hide_reveal_timer.isActive():
                    self._auto_hide_reveal_timer.start(max(0, int(self._behavior_settings.auto_hide_reveal_delay_ms)))
            else:
                self._auto_hide_reveal_timer.stop()
            return

        self._auto_hide_reveal_timer.stop()
        if keep_visible:
            self._auto_hide_hide_timer.stop()
            return
        if not self._auto_hide_hide_timer.isActive():
            self._auto_hide_hide_timer.start(max(0, int(self._behavior_settings.auto_hide_hide_delay_ms)))

    def _reveal_topbar(self, immediate: bool = False) -> None:
        if not self._auto_hide_enabled:
            self._apply_visibility_state(hidden=False, animated=False)
            return
        self._auto_hide_reveal_timer.stop()
        self._apply_visibility_state(hidden=False, animated=not immediate)

    def _hide_topbar_to_edge(self) -> None:
        if not self._auto_hide_enabled:
            return
        if self._has_attached_popup():
            return
        if self._pointer_is_over_topbar(QCursor.pos()):
            return
        self._auto_hide_hide_timer.stop()
        self._apply_visibility_state(hidden=True, animated=True)

    def _apply_x11_panel_hints(self) -> None:
        if not self.isVisible():
            return
        if QGuiApplication.platformName().lower() != "xcb":
            return

        wid = int(self.winId())
        reservation = build_top_strut_reservation(
            window_rect=self.frameGeometry(),
            screen_rect=self._screen_geometry(),
            reserve_height=max(0, self._current_reserved_height()),
        )
        self._set_x11_dock_and_strut_properties(
            wid,
            reservation.strut,
            reservation.strut_partial,
        )
        self._x11_panel_hints_applied = True

    def _set_x11_dock_and_strut_properties(self, wid: int, strut, strut_partial) -> None:
        try:
            X, Xatom, display = load_xlib()
            x_display = display.Display()
        except Exception as exc:
            LOGGER.warning("Could not connect to X11 for panel hints: %r", exc)
            return

        try:
            window = x_display.create_resource_object("window", wid)
            window_type_atom = x_display.intern_atom("_NET_WM_WINDOW_TYPE")
            window_type_dock_atom = x_display.intern_atom("_NET_WM_WINDOW_TYPE_DOCK")
            state_atom = x_display.intern_atom("_NET_WM_STATE")
            state_above_atom = x_display.intern_atom("_NET_WM_STATE_ABOVE")
            state_sticky_atom = x_display.intern_atom("_NET_WM_STATE_STICKY")
            strut_atom = x_display.intern_atom("_NET_WM_STRUT")
            strut_partial_atom = x_display.intern_atom("_NET_WM_STRUT_PARTIAL")

            window.change_property(
                window_type_atom,
                Xatom.ATOM,
                32,
                [window_type_dock_atom],
                X.PropModeReplace,
            )
            window.change_property(
                state_atom,
                Xatom.ATOM,
                32,
                [state_above_atom, state_sticky_atom],
                X.PropModeReplace,
            )
            window.change_property(
                strut_atom,
                Xatom.CARDINAL,
                32,
                [int(value) for value in strut],
                X.PropModeReplace,
            )
            window.change_property(
                strut_partial_atom,
                Xatom.CARDINAL,
                32,
                [int(value) for value in strut_partial],
                X.PropModeReplace,
            )
            x_display.flush()
            x_display.sync()
        except Exception as exc:
            LOGGER.warning("Failed to apply X11 dock/strut hints to window %s: %r", wid, exc)
        finally:
            try:
                x_display.close()
            except Exception:
                pass

    @Slot()
    def _open_terminal(self) -> None:
        ok, message = launch_background_command("pytpo-terminal")
        if ok:
            LOGGER.info("Launched terminal via %s", message)
            return
        QMessageBox.warning(self, "Launch Failed", f"Could not start the terminal.\n\n{message}")

    @Slot()
    def _open_dock_panel(self) -> None:
        ok, message = launch_background_command("pytpo-dock")
        if ok:
            LOGGER.info("Launched dock panel via %s", message)
            return
        QMessageBox.warning(self, "Launch Failed", f"Could not start the dock panel.\n\n{message}")
