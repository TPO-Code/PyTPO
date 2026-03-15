from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame

from .settings import PanelVisualSettings


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


def color_with_opacity(value: str, opacity_percent: int, fallback: str) -> QColor:
    color = color_from_setting(value, fallback)
    alpha = max(0, min(255, round(color.alpha() * max(0, min(100, int(opacity_percent))) / 100.0)))
    color.setAlpha(alpha)
    return color


def css_color(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alphaF():.3f})"


class StyledPanelFrame(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings = PanelVisualSettings(
            background_color="#101317ff",
            background_opacity=100,
            background_image_path="",
            background_image_opacity=100,
            background_image_fit="cover",
            background_tint="#00000000",
            border_color="#2e3742ff",
            border_width=1,
            border_radius=18,
            border_style="solid",
        )
        self._background_pixmap = QPixmap()

    def apply_style_settings(self, settings: PanelVisualSettings) -> None:
        self._settings = settings
        image_path = str(settings.background_image_path or "").strip()
        if image_path:
            pixmap = QPixmap(image_path)
            self._background_pixmap = pixmap if not pixmap.isNull() else QPixmap()
        else:
            self._background_pixmap = QPixmap()
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        border_width = max(0, int(self._settings.border_width))
        border_offset = border_width / 2.0
        rect = QRectF(self.rect()).adjusted(border_offset, border_offset, -border_offset, -border_offset)
        radius = max(0, int(self._settings.border_radius))

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        painter.fillPath(
            path,
            color_with_opacity(
                self._settings.background_color,
                self._settings.background_opacity,
                "#101317ff",
            ),
        )

        if not self._background_pixmap.isNull():
            target_rect = rect.toRect()
            fit_mode = str(self._settings.background_image_fit or "cover").strip().lower()
            image_opacity = max(0.0, min(1.0, int(self._settings.background_image_opacity) / 100.0))
            painter.setOpacity(image_opacity)
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
            painter.setOpacity(1.0)

        tint = color_from_setting(self._settings.background_tint, "#00000000")
        if tint.alpha() > 0:
            painter.fillPath(path, tint)

        painter.setClipping(False)
        if border_width > 0:
            pen = QPen(color_from_setting(self._settings.border_color, "#2e3742ff"))
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
