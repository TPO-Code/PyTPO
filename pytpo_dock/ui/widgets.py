from __future__ import annotations

import os

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QStyle, QToolButton, QVBoxLayout, QWidget

from ..apps import launch_app
from ..debug import log_dock_debug
from ..settings_dialog import DockVisualSettings


def build_settings_icon():
    """Find a reasonable themed settings icon with a Qt fallback."""
    for icon_name in ('settings', 'preferences-system', 'configure'):
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return icon
    return QApplication.style().standardIcon(QStyle.SP_FileDialogDetailedView)


def color_from_setting(value: str, fallback: str) -> QColor:
    text = str(value or "").strip()
    if len(text) == 9 and text.startswith("#"):
        try:
            return QColor(
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
                int(text[7:9], 16),
            )
        except Exception:
            pass
    color = QColor(text)
    if color.isValid():
        return color
    return QColor(fallback)


def themed_icon(icon_names: tuple[str, ...], fallback_standard_icon) -> QIcon:
    for icon_name in icon_names:
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return icon
    return QApplication.style().standardIcon(fallback_standard_icon)


class DockItem(QToolButton):
    pin_toggled = Signal(str, bool)
    preview_requested = Signal(object)
    preview_hidden = Signal()

    def __init__(
        self,
        app_data,
        is_pinned=False,
        is_running=False,
        win_id=None,
        windows=None,
        *,
        icon_size=42,
        indicator_mode="dots",
    ):
        super().__init__()
        self.app_data = app_data
        self.is_pinned = is_pinned
        self.is_running = is_running
        self.win_id = win_id
        self.windows = windows or []
        self.icon_size_px = max(16, int(icon_size))
        self.indicator_mode = str(indicator_mode or "dots").strip().lower()
        self.button_size = max(48, self.icon_size_px + 18)
        self._last_paint_signature = None

        self.setFixedSize(self.button_size, self.button_size)

        icon = QIcon()
        for icon_name in (
            app_data.get('Icon', ''),
            app_data.get('StartupWMClass', ''),
            'application-x-executable',
            'application-default-icon',
        ):
            if not icon_name:
                continue
            themed_icon = QIcon.fromTheme(icon_name)
            if not themed_icon.isNull():
                icon = themed_icon
                break
            if os.path.exists(icon_name):
                file_icon = QIcon(icon_name)
                if not file_icon.isNull():
                    icon = file_icon
                    break

        if icon.isNull():
            icon = QApplication.style().standardIcon(QStyle.SP_DesktopIcon)

        self.setIcon(icon)
        self.setIconSize(QSize(self.icon_size_px, self.icon_size_px))

        tooltip = app_data.get('Name', 'Unknown App')
        title = app_data.get('Title', '').strip()
        if title and title != tooltip:
            tooltip = f"{tooltip}\n{title}"
        self.setToolTip(tooltip)

        hover_radius = max(10, self.button_size // 5)
        self.setStyleSheet("""
            QToolButton {
                background: transparent;
                border-radius: %dpx;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 30);
            }
            QToolButton:pressed {
                background: rgba(255, 255, 255, 15);
            }
        """ % hover_radius)

        self.clicked.connect(self.launch_or_focus)

    def launch_or_focus(self):
        if self.is_running and self.win_id:
            import subprocess

            subprocess.run(['wmctrl', '-i', '-a', self.win_id])
            return

        launch_app(self.app_data)

    def contextMenuEvent(self, event):
        if self.app_data.get('runtime_only'):
            return

        menu = QMenu(self)
        pin_text = "Unpin from Dock" if self.is_pinned else "Pin to Dock"
        pin_action = QAction(pin_text, self)
        pin_action.triggered.connect(self.toggle_pin)
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
        menu.exec(event.globalPos())

    def toggle_pin(self):
        if self.app_data.get('runtime_only'):
            return
        self.pin_toggled.emit(self.app_data['path'], not self.is_pinned)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.is_running:
            return

        count = len(self.windows) if self.windows else 1
        paint_signature = (
            self.app_data.get('Name', 'Unknown App'),
            count,
            self.indicator_mode,
            self.width(),
            self.height(),
        )
        if paint_signature != self._last_paint_signature:
            self._last_paint_signature = paint_signature
            log_dock_debug(
                "dock-item-paint",
                app_name=self.app_data.get('Name', 'Unknown App'),
                is_running=self.is_running,
                indicator_mode=self.indicator_mode,
                window_count=count,
                button_size=(self.width(), self.height()),
                icon_size=(self.iconSize().width(), self.iconSize().height()),
            )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(200, 200, 200))
        painter.setPen(Qt.NoPen)
        if self.indicator_mode == "numbers":
            text = str(count)
            badge_height = 14
            text_width = painter.fontMetrics().horizontalAdvance(text)
            badge_width = max(16, text_width + 8)
            rect = QRect(
                (self.width() - badge_width) // 2,
                self.height() - badge_height - 4,
                badge_width,
                badge_height,
            )
            painter.drawRoundedRect(rect, badge_height / 2, badge_height / 2)
            painter.setPen(QColor(25, 25, 25))
            painter.drawText(rect, Qt.AlignCenter, text)
            return

        if count > 4:
            bar_width = 18
            bar_height = 4
            rect = QRect(
                (self.width() - bar_width) // 2,
                self.height() - bar_height - 6,
                bar_width,
                bar_height,
            )
            painter.drawRoundedRect(rect, 2, 2)
            return

        dot_size = 4
        spacing = 3
        total_width = count * dot_size + max(0, count - 1) * spacing
        start_x = (self.width() - total_width) // 2
        y = self.height() - dot_size - 4
        for index in range(count):
            painter.drawEllipse(start_x + index * (dot_size + spacing), y, dot_size, dot_size)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.is_running and self.win_id:
            self.preview_requested.emit(self)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.preview_hidden.emit()


class WindowPreview(QFrame):
    hover_changed = Signal(bool)
    action_requested = Signal(str, object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WindowPreview")
        self._content_size = QSize()
        self.setStyleSheet("""
            QFrame#WindowPreview {
                background-color: rgba(20, 20, 20, 235);
                border: 1px solid rgba(255, 255, 255, 35);
                border-radius: 14px;
            }
            QLabel {
                color: white;
                background: transparent;
            }
        """)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(10)

    def clear_content(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def update_content(self, previews):
        self.clear_content()
        log_dock_debug(
            "dock-preview-content-updated",
            preview_count=len(previews),
            preview_titles=[preview.get('title', '') for preview in previews],
        )
        frame_sizes = []
        for preview in previews:
            frame = QFrame(self)
            frame.setStyleSheet("""
                QFrame {
                    background-color: rgba(255, 255, 255, 12);
                    border-radius: 10px;
                }
            """)

            image_label = QLabel(frame)
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setMinimumSize(220, 140)

            title_label = QLabel(frame)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setWordWrap(True)

            controls_row = QHBoxLayout()
            controls_row.setContentsMargins(0, 0, 0, 0)
            controls_row.setSpacing(4)

            scaled = preview['pixmap'].scaled(280, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            image_label.setPixmap(scaled)
            title_label.setText(preview['title'])

            for action_name, tooltip, icon in self._actions_for_preview(preview):
                button = QToolButton(frame)
                button.setCursor(Qt.PointingHandCursor)
                button.setAutoRaise(True)
                button.setIcon(icon)
                button.setIconSize(QSize(14, 14))
                button.setFixedSize(24, 24)
                button.setToolTip(tooltip)
                button.setStyleSheet("""
                    QToolButton {
                        background: rgba(255, 255, 255, 20);
                        border-radius: 6px;
                    }
                    QToolButton:hover {
                        background: rgba(255, 255, 255, 35);
                    }
                """)
                button.clicked.connect(
                    lambda _checked=False, action=action_name, data=dict(preview): self.action_requested.emit(action, data)
                )
                controls_row.addWidget(button)
            controls_row.addStretch(1)

            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(8, 8, 8, 8)
            frame_layout.setSpacing(6)
            frame_layout.addWidget(image_label)
            frame_layout.addWidget(title_label)
            frame_layout.addLayout(controls_row)
            self.layout.addWidget(frame)
            frame_layout.activate()
            frame.adjustSize()
            frame_size = frame.sizeHint().expandedTo(frame.minimumSizeHint())
            frame_sizes.append(frame_size)

        self.layout.invalidate()
        self.layout.activate()
        margins = self.layout.contentsMargins()
        total_width = margins.left() + margins.right()
        total_height = margins.top() + margins.bottom()
        if frame_sizes:
            total_width += sum(size.width() for size in frame_sizes)
            total_width += self.layout.spacing() * max(0, len(frame_sizes) - 1)
            total_height += max(size.height() for size in frame_sizes)
        self._content_size = QSize(total_width, total_height)
        self.setMinimumSize(self._content_size)
        self.resize(self._content_size)
        self.updateGeometry()
        log_dock_debug(
            "dock-preview-size-computed",
            frame_sizes=[(size.width(), size.height()) for size in frame_sizes],
            target_size=(self._content_size.width(), self._content_size.height()),
        )

    def sizeHint(self):
        if self._content_size.isValid() and not self._content_size.isEmpty():
            return self._content_size
        return super().sizeHint()

    def minimumSizeHint(self):
        if self._content_size.isValid() and not self._content_size.isEmpty():
            return self._content_size
        return super().minimumSizeHint()

    def _actions_for_preview(self, preview):
        is_maximized = bool(preview.get('is_maximized'))
        maximize_icon = themed_icon(
            ('window-restore', 'view-restore') if is_maximized else ('window-maximize', 'view-fullscreen'),
            QStyle.SP_TitleBarNormalButton if is_maximized else QStyle.SP_TitleBarMaxButton,
        )
        maximize_tooltip = "Restore window" if is_maximized else "Maximize window"
        return [
            (
                'focus',
                "Focus window",
                themed_icon(('go-jump', 'go-up'), QStyle.SP_ArrowUp),
            ),
            (
                'minimize',
                "Minimize window",
                themed_icon(('window-minimize',), QStyle.SP_TitleBarMinButton),
            ),
            (
                'toggle_maximize',
                maximize_tooltip,
                maximize_icon,
            ),
            (
                'close',
                "Close window",
                themed_icon(('window-close',), QStyle.SP_TitleBarCloseButton),
            ),
            (
                'new_window',
                "Open new window",
                themed_icon(('window-new', 'list-add'), QStyle.SP_FileDialogNewFolder),
            ),
        ]

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hover_changed.emit(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.hover_changed.emit(False)


class DockContainerFrame(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = DockVisualSettings()
        self._background_pixmap = QPixmap()
        self._last_paint_signature = None

    def apply_settings(self, settings: DockVisualSettings):
        self._settings = settings
        image_path = str(settings.background_image_path or "").strip()
        if image_path:
            pixmap = QPixmap(image_path)
            self._background_pixmap = pixmap if not pixmap.isNull() else QPixmap()
        else:
            self._background_pixmap = QPixmap()
        log_dock_debug(
            "dock-container-settings-applied",
            background_color=settings.background_color,
            background_image_path=image_path,
            background_image_loaded=not self._background_pixmap.isNull(),
            background_fit=settings.background_image_fit,
            background_tint=settings.background_tint,
            border_color=settings.border_color,
            border_width=settings.border_width,
            border_radius=settings.border_radius,
            border_style=settings.border_style,
        )
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        border_width = max(0, int(self._settings.border_width))
        border_offset = border_width / 2.0
        rect = QRectF(self.rect()).adjusted(border_offset, border_offset, -border_offset, -border_offset)
        radius = max(0, int(self._settings.border_radius))
        paint_signature = (
            self.width(),
            self.height(),
            border_width,
            radius,
            self._settings.background_color,
            self._settings.background_image_fit,
            self._settings.background_tint,
            self._settings.border_color,
            self._settings.border_style,
            self._background_pixmap.width(),
            self._background_pixmap.height(),
        )
        if paint_signature != self._last_paint_signature:
            self._last_paint_signature = paint_signature
            log_dock_debug(
                "dock-container-paint",
                rect=(rect.x(), rect.y(), rect.width(), rect.height()),
                widget_rect=self.rect().getRect(),
                border_width=border_width,
                radius=radius,
                has_background_image=not self._background_pixmap.isNull(),
                background_image_size=(self._background_pixmap.width(), self._background_pixmap.height()),
                fit_mode=self._settings.background_image_fit,
            )

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        painter.fillPath(path, color_from_setting(self._settings.background_color, "#1e1e1e"))

        if not self._background_pixmap.isNull():
            target_rect = rect.toRect()
            fit_mode = str(self._settings.background_image_fit or "cover").strip().lower()
            if fit_mode == "tile":
                painter.drawTiledPixmap(target_rect, self._background_pixmap)
            elif fit_mode == "stretch":
                painter.drawPixmap(target_rect, self._background_pixmap, self._background_pixmap.rect())
            else:
                aspect_mode = Qt.KeepAspectRatioByExpanding if fit_mode == "cover" else Qt.KeepAspectRatio
                scaled = self._background_pixmap.scaled(target_rect.size(), aspect_mode, Qt.SmoothTransformation)
                draw_x = target_rect.x() + (target_rect.width() - scaled.width()) // 2
                draw_y = target_rect.y() + (target_rect.height() - scaled.height()) // 2
                if fit_mode == "center":
                    scaled = self._background_pixmap
                    draw_x = target_rect.x() + (target_rect.width() - scaled.width()) // 2
                    draw_y = target_rect.y() + (target_rect.height() - scaled.height()) // 2
                painter.drawPixmap(draw_x, draw_y, scaled)

        tint = color_from_setting(self._settings.background_tint, "#00000000")
        if tint.alpha() > 0:
            painter.fillPath(path, tint)

        painter.setClipping(False)
        if border_width > 0:
            pen = QPen(color_from_setting(self._settings.border_color, "#ffffff33"))
            pen.setWidth(border_width)
            border_style = str(self._settings.border_style or "solid").strip().lower()
            if border_style == "dashed":
                pen.setStyle(Qt.DashLine)
            elif border_style == "dotted":
                pen.setStyle(Qt.DotLine)
            else:
                pen.setStyle(Qt.SolidLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
