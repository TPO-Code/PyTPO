from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .desktop_apps import (
    DESKTOP_APP_DRAG_MIME_TYPE,
    DesktopApplication,
    build_desktop_app_drag_payload,
    launch_app,
    load_desktop_applications,
)
from .settings import load_appgrid_settings
from .styled_panel import StyledPanelFrame, color_from_setting, color_with_opacity, css_color


_CATEGORY_LABELS = {
    "All": "All Applications",
    "Accessibility": "Accessibility",
    "AudioVideo": "Audio & Video",
    "Development": "Development",
    "Education": "Education",
    "Game": "Games",
    "Graphics": "Graphics",
    "Network": "Network",
    "Office": "Office",
    "Science": "Science",
    "Settings": "Settings",
    "System": "System",
    "Utility": "Utilities",
    "Other": "Other",
}

_PRIMARY_CATEGORIES = (
    "AudioVideo",
    "Development",
    "Education",
    "Game",
    "Graphics",
    "Network",
    "Office",
    "Science",
    "Settings",
    "System",
    "Utility",
    "Accessibility",
)


def _category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category.replace("-", " ").strip() or "Other")


def _primary_category(app: DesktopApplication) -> str:
    for category in app.categories:
        if category in _PRIMARY_CATEGORIES:
            return category
    if app.categories:
        return app.categories[0]
    return "Other"


def _mix_colors(base: QColor, overlay: QColor, weight: float) -> QColor:
    clamped = max(0.0, min(1.0, float(weight)))
    return QColor(
        round(base.red() * (1.0 - clamped) + overlay.red() * clamped),
        round(base.green() * (1.0 - clamped) + overlay.green() * clamped),
        round(base.blue() * (1.0 - clamped) + overlay.blue() * clamped),
        round(base.alpha() * (1.0 - clamped) + overlay.alpha() * clamped),
    )


class AppGridListWidget(QListWidget):
    drag_finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_start_position: QPoint | None = None
        self.drag_in_progress = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_start_position = event.position().toPoint()
        else:
            self._drag_start_position = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_position is None:
            super().mouseMoveEvent(event)
            return

        current_position = event.position().toPoint()
        if (current_position - self._drag_start_position).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        source_item = self.itemAt(self._drag_start_position)
        if source_item is None:
            self._drag_start_position = None
            super().mouseMoveEvent(event)
            return

        app = source_item.data(Qt.UserRole)
        if not isinstance(app, DesktopApplication):
            self._drag_start_position = None
            super().mouseMoveEvent(event)
            return

        self._drag_start_position = None
        self._start_drag(app, source_item)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start_position = None
        super().mouseReleaseEvent(event)

    def _start_drag(self, app: DesktopApplication, item: QListWidgetItem) -> None:
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(
            DESKTOP_APP_DRAG_MIME_TYPE,
            QByteArray(build_desktop_app_drag_payload(app)),
        )
        mime_data.setText(app.path)
        drag.setMimeData(mime_data)

        pixmap = self._drag_pixmap(item)
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(pixmap.rect().center())

        self.drag_in_progress = True
        try:
            drag.exec(Qt.CopyAction)
        finally:
            self.drag_in_progress = False
            self.drag_finished.emit()

    def _drag_pixmap(self, item: QListWidgetItem) -> QPixmap:
        icon = item.icon()
        if icon.isNull():
            return QPixmap()
        return icon.pixmap(self.iconSize())


class AppGridWindow(QWidget):
    def __init__(
        self,
        *,
        visual_settings=None,
        close_on_focus_loss: bool = True,
        quit_on_close: bool = True,
    ) -> None:
        super().__init__()
        self._close_on_focus_loss = False
        self._close_on_focus_loss_enabled = bool(close_on_focus_loss)
        self._quit_on_close = bool(quit_on_close)
        self._all_apps = load_desktop_applications()
        self._category_counts = Counter(_primary_category(app) for app in self._all_apps)
        self._visual_settings = visual_settings if visual_settings is not None else load_appgrid_settings()

        self.setWindowTitle("Applications")
        self.setMinimumSize(920, 620)
        self.resize(1080, 700)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.window_frame = StyledPanelFrame(self)
        root_layout.addWidget(self.window_frame, 1)

        content_root = QVBoxLayout(self.window_frame)
        content_root.setContentsMargins(18, 18, 18, 18)
        content_root.setSpacing(14)

        self.search_input = QLineEdit(self.window_frame)
        self.search_input.setPlaceholderText("Search applications")
        self.search_input.textChanged.connect(self._refresh_apps)

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        self.category_frame = StyledPanelFrame(self.window_frame)
        category_layout = QVBoxLayout(self.category_frame)
        category_layout.setContentsMargins(8, 8, 8, 8)
        category_layout.setSpacing(0)
        self.category_list = QListWidget(self.category_frame)
        self.category_list.setFixedWidth(240)
        self.category_list.currentRowChanged.connect(self._refresh_apps)
        category_layout.addWidget(self.category_list)

        self.app_frame = StyledPanelFrame(self.window_frame)
        app_layout = QVBoxLayout(self.app_frame)
        app_layout.setContentsMargins(8, 8, 8, 8)
        app_layout.setSpacing(0)
        self.app_list = AppGridListWidget(self.app_frame)
        self.app_list.setViewMode(QListWidget.IconMode)
        self.app_list.setResizeMode(QListWidget.Adjust)
        self.app_list.setMovement(QListWidget.Static)
        self.app_list.setSelectionMode(QListWidget.SingleSelection)
        self.app_list.setWordWrap(True)
        self.app_list.itemActivated.connect(self._launch_item)
        self.app_list.drag_finished.connect(self._handle_drag_finished)
        self.app_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        app_layout.addWidget(self.app_list)

        content_layout.addWidget(self.category_frame)
        content_layout.addWidget(self.app_frame, 1)

        content_root.addWidget(self.search_input)
        content_root.addLayout(content_layout, 1)

        self._apply_visual_settings()
        self._populate_categories()
        if self.category_list.count():
            self.category_list.setCurrentRow(0)
        self.search_input.setFocus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(available.center() - self.rect().center())
        self.raise_()
        self.activateWindow()
        if self._close_on_focus_loss_enabled:
            QTimer.singleShot(0, self._enable_focus_loss_close)

    def event(self, event) -> bool:
        if (
            event.type() == QEvent.WindowDeactivate
            and self._close_on_focus_loss
            and not self.app_list.drag_in_progress
        ):
            QTimer.singleShot(0, self.close)
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() in {Qt.Key_Return, Qt.Key_Enter} and self.app_list.currentItem() is not None:
            self._launch_item(self.app_list.currentItem())
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
        app = QApplication.instance()
        if self._quit_on_close and app is not None:
            QTimer.singleShot(0, app.quit)

    def update_visual_settings(self, visual_settings) -> None:
        self._visual_settings = visual_settings
        self._apply_visual_settings()
        self.app_list.viewport().update()
        self.category_list.viewport().update()
        self.window_frame.update()
        self.category_frame.update()
        self.app_frame.update()

    def _apply_visual_settings(self) -> None:
        settings = self._visual_settings
        self.window_frame.apply_style_settings(settings.window)
        self.category_frame.apply_style_settings(settings.side_panel)
        self.app_frame.apply_style_settings(settings.app_panel)

        tile_gap = max(0, int(settings.tile_spacing))
        tile_width = max(96, settings.icon_size + 52) + tile_gap * 2
        tile_height = max(90, settings.icon_size + 38) + tile_gap * 2
        self.app_list.setIconSize(QSize(settings.icon_size, settings.icon_size))
        self.app_list.setSpacing(0)
        self.app_list.setGridSize(QSize(tile_width, tile_height))

        font_color = color_from_setting(settings.font_color, "#f2f4f8ff")
        highlight_color = color_from_setting(settings.highlight_color, "#284b63ff")
        highlight_hover = _mix_colors(highlight_color, QColor(255, 255, 255, 255), 0.16)
        placeholder_color = _mix_colors(font_color, QColor(0, 0, 0, 255), 0.35)
        scrollbar_base = color_with_opacity(settings.window.background_color, settings.window.background_opacity, "#101317ff")
        scrollbar_hover = _mix_colors(scrollbar_base, QColor(255, 255, 255, 255), 0.18)
        scrollbar_track = _mix_colors(scrollbar_base, QColor(0, 0, 0, 255), 0.22)
        search_bg = color_from_setting(settings.search_box.background_color, "#181d24ff")
        search_border = color_from_setting(settings.search_box.border_color, "#2e3742ff")

        list_common = f"""
            QListWidget {{
                background: transparent;
                border: none;
                color: {css_color(font_color)};
                outline: none;
                padding: 4px;
            }}
            QListWidget::item {{
                border-radius: 10px;
                padding: 8px;
            }}
            QListWidget::item:selected {{
                background: {css_color(highlight_color)};
                color: {css_color(font_color)};
            }}
            QListWidget::item:hover {{
                background: {css_color(highlight_hover)};
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background: {css_color(scrollbar_track)};
                border: none;
                margin: 0px;
            }}
            QScrollBar:vertical {{
                width: 12px;
            }}
            QScrollBar:horizontal {{
                height: 12px;
            }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background: {css_color(scrollbar_base)};
                border-radius: 6px;
                min-height: 24px;
                min-width: 24px;
            }}
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
                background: {css_color(scrollbar_hover)};
            }}
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
                background: transparent;
                border: none;
            }}
        """
        self.category_list.setStyleSheet(list_common)
        self.app_list.setStyleSheet(list_common)

        self.search_input.setStyleSheet(
            f"""
            QLineEdit {{
                background: {css_color(search_bg)};
                color: {css_color(font_color)};
                border: {max(0, settings.search_box.border_width)}px solid {css_color(search_border)};
                border-radius: {max(0, settings.search_box.border_radius)}px;
                padding: 10px 12px;
                selection-background-color: {css_color(highlight_color)};
                selection-color: {css_color(font_color)};
            }}
            QLineEdit::placeholder {{
                color: {css_color(placeholder_color)};
            }}
            """
        )

    def _enable_focus_loss_close(self) -> None:
        self._close_on_focus_loss = True

    def _handle_drag_finished(self) -> None:
        if self._close_on_focus_loss and not self.isActiveWindow():
            QTimer.singleShot(0, self.close)

    def _populate_categories(self) -> None:
        self.category_list.clear()
        ordered_categories = ["All"]
        ordered_categories.extend(
            sorted(
                self._category_counts,
                key=lambda category: (_category_label(category).casefold(), category.casefold()),
            )
        )
        for category in ordered_categories:
            count = len(self._all_apps) if category == "All" else self._category_counts.get(category, 0)
            item = QListWidgetItem(f"{_category_label(category)} ({count})")
            item.setData(Qt.UserRole, category)
            self.category_list.addItem(item)

    def _selected_category(self) -> str:
        item = self.category_list.currentItem()
        if item is None:
            return "All"
        return str(item.data(Qt.UserRole) or "All")

    def _refresh_apps(self) -> None:
        selected_category = self._selected_category()
        query = self.search_input.text().strip().casefold()
        self.app_list.clear()

        filtered_apps = []
        for app in self._all_apps:
            if selected_category != "All" and _primary_category(app) != selected_category:
                continue
            haystack = " ".join(
                (
                    app.name,
                    app.generic_name,
                    app.comment,
                    " ".join(app.categories),
                    " ".join(app.keywords),
                )
            ).casefold()
            if query and query not in haystack:
                continue
            filtered_apps.append(app)

        for app in filtered_apps:
            item = QListWidgetItem(self._icon_for_app(app), app.name)
            item.setToolTip(self._tooltip_for_app(app))
            item.setTextAlignment(Qt.AlignHCenter)
            item.setData(Qt.UserRole, app)
            self.app_list.addItem(item)

        if self.app_list.count():
            self.app_list.setCurrentRow(0)

    def _icon_for_app(self, app: DesktopApplication) -> QIcon:
        for icon_name in (app.icon, app.startup_wm_class, "application-x-executable", "application-default-icon"):
            if not icon_name:
                continue
            themed_icon = QIcon.fromTheme(icon_name)
            if not themed_icon.isNull():
                return themed_icon
            if app.icon and icon_name == app.icon:
                file_icon = QIcon(app.icon)
                if not file_icon.isNull():
                    return file_icon
        return QApplication.style().standardIcon(QStyle.SP_DesktopIcon)

    def _tooltip_for_app(self, app: DesktopApplication) -> str:
        lines = [app.name]
        if app.generic_name and app.generic_name != app.name:
            lines.append(app.generic_name)
        if app.comment:
            lines.append(app.comment)
        if app.categories:
            lines.append(", ".join(_category_label(category) for category in app.categories))
        return "\n".join(lines)

    def _launch_item(self, item: QListWidgetItem) -> None:
        app = item.data(Qt.UserRole)
        if not isinstance(app, DesktopApplication):
            return
        if launch_app(app):
            self.close()


def build_window(
    *,
    visual_settings=None,
    close_on_focus_loss: bool = True,
    quit_on_close: bool = True,
) -> AppGridWindow:
    return AppGridWindow(
        visual_settings=visual_settings,
        close_on_focus_loss=close_on_focus_loss,
        quit_on_close=quit_on_close,
    )
