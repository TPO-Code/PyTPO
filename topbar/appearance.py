from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect

from .settings import TopBarBehaviorSettings

_MAX_WIDGET_SIZE = 16_777_215


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
            return QColor(fallback)
    color = QColor(text)
    if color.isValid():
        return color
    return QColor(fallback)


def apply_color_opacity(color: QColor, opacity_percent: int) -> QColor:
    adjusted = QColor(color)
    alpha = int(
        round(
            max(0.0, min(1.0, adjusted.alphaF() * max(0.0, min(1.0, int(opacity_percent) / 100.0))))
            * 255
        )
    )
    adjusted.setAlpha(alpha)
    return adjusted


def color_to_qss_rgba(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def load_background_pixmap(image_path: str) -> QPixmap:
    normalized = str(image_path or "").strip()
    if not normalized:
        return QPixmap()
    pixmap = QPixmap(normalized)
    return pixmap if not pixmap.isNull() else QPixmap()


def _blur_pixmap(pixmap: QPixmap, blur_amount: int) -> QPixmap:
    if pixmap.isNull() or blur_amount <= 0:
        return pixmap
    divisor = max(1, 1 + int(blur_amount / 3))
    reduced = pixmap.scaled(
        max(1, pixmap.width() // divisor),
        max(1, pixmap.height() // divisor),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )
    return reduced.scaled(pixmap.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def _draw_aligned_pixmap(
    painter: QPainter,
    target_rect: QRect,
    pixmap: QPixmap,
    *,
    fit_mode: str,
    alignment: str,
) -> None:
    fit = str(fit_mode or "cover").strip().lower()
    align = str(alignment or "center").strip().lower()
    if fit == "tile":
        painter.drawTiledPixmap(target_rect, pixmap)
        return
    if fit in {"fill", "stretch"}:
        painter.drawPixmap(target_rect, pixmap, pixmap.rect())
        return

    if fit == "center":
        scaled = pixmap
    else:
        aspect_mode = Qt.KeepAspectRatioByExpanding if fit == "cover" else Qt.KeepAspectRatio
        scaled = pixmap.scaled(target_rect.size(), aspect_mode, Qt.SmoothTransformation)

    draw_x = target_rect.x() + (target_rect.width() - scaled.width()) // 2
    draw_y = target_rect.y() + (target_rect.height() - scaled.height()) // 2
    if align == "top":
        draw_y = target_rect.y()
    elif align == "bottom":
        draw_y = target_rect.bottom() - scaled.height() + 1
    elif align == "left":
        draw_x = target_rect.x()
    elif align == "right":
        draw_x = target_rect.right() - scaled.width() + 1
    painter.drawPixmap(draw_x, draw_y, scaled)


@dataclass(slots=True)
class PanelAppearance:
    background_type: str = "solid"
    background_color: str = "#5b5b5b"
    gradient_start_color: str = "#5b5b5b"
    gradient_end_color: str = "#3f3f3f"
    gradient_direction: str = "horizontal"
    background_opacity: int = 100
    background_blur: int = 0
    background_image_path: str = ""
    image_fit_mode: str = "cover"
    image_alignment: str = "center"
    image_opacity: int = 100
    overlay_tint: str = "#000000"
    overlay_tint_opacity: int = 0
    corner_radius: int = 0
    show_border: bool = False
    border_width: int = 1
    border_color: str = "#ffffff"
    border_opacity: int = 18
    show_shadow: bool = False
    shadow_blur: int = 20
    shadow_offset_x: int = 0
    shadow_offset_y: int = 4
    shadow_opacity: int = 30
    fixed_height: int | None = None


def build_panel_appearance(
    source: Any,
    prefix: str,
    *,
    fixed_height_key: str | None = None,
    corner_radius_key: str = "corner_radius",
) -> PanelAppearance:
    base = str(prefix or "").strip().rstrip("_")

    def _read(name: str, fallback: Any) -> Any:
        return getattr(source, f"{base}_{name}", fallback)

    fixed_height = None
    if fixed_height_key:
        fixed_height = getattr(source, f"{base}_{fixed_height_key}", None)

    return PanelAppearance(
        background_type=_read("background_type", "solid"),
        background_color=_read("background_color", "#5b5b5b"),
        gradient_start_color=_read("gradient_start_color", "#5b5b5b"),
        gradient_end_color=_read("gradient_end_color", "#3f3f3f"),
        gradient_direction=_read("gradient_direction", "horizontal"),
        background_opacity=int(_read("background_opacity", 100)),
        background_blur=int(_read("background_blur", 0)),
        background_image_path=_read("background_image_path", ""),
        image_fit_mode=_read("image_fit_mode", "cover"),
        image_alignment=_read("image_alignment", "center"),
        image_opacity=int(_read("image_opacity", 100)),
        overlay_tint=_read("overlay_tint", "#000000"),
        overlay_tint_opacity=int(_read("overlay_tint_opacity", 0)),
        corner_radius=int(_read(corner_radius_key, 0)),
        show_border=bool(_read("show_border", False)),
        border_width=int(_read("border_width", 1)),
        border_color=_read("border_color", "#ffffff"),
        border_opacity=int(_read("border_opacity", 18)),
        show_shadow=bool(_read("show_shadow", False)),
        shadow_blur=int(_read("shadow_blur", 20)),
        shadow_offset_x=int(_read("shadow_offset_x", 0)),
        shadow_offset_y=int(_read("shadow_offset_y", 4)),
        shadow_opacity=int(_read("shadow_opacity", 30)),
        fixed_height=None if fixed_height is None else int(fixed_height),
    )


class StyledPanel(QFrame):
    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self._appearance = PanelAppearance()
        self._background_pixmap = QPixmap()
        self._blurred_pixmap = QPixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def apply_panel_appearance(self, appearance: PanelAppearance) -> None:
        self._appearance = appearance
        fixed_height = appearance.fixed_height
        if fixed_height is None:
            self.setMinimumHeight(0)
            self.setMaximumHeight(_MAX_WIDGET_SIZE)
        else:
            height = max(1, int(fixed_height))
            self.setFixedHeight(height)
        self._background_pixmap = load_background_pixmap(appearance.background_image_path)
        self._blurred_pixmap = _blur_pixmap(self._background_pixmap, int(appearance.background_blur))
        self._apply_shadow_effect()
        self.update()

    def _apply_shadow_effect(self) -> None:
        if not self._appearance.show_shadow or int(self._appearance.shadow_opacity) <= 0:
            self.setGraphicsEffect(None)
            return
        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(self)
            self.setGraphicsEffect(effect)
        effect.setBlurRadius(max(0, int(self._appearance.shadow_blur)))
        effect.setOffset(int(self._appearance.shadow_offset_x), int(self._appearance.shadow_offset_y))
        shadow_color = QColor(0, 0, 0)
        shadow_color.setAlpha(
            int(round(max(0.0, min(1.0, int(self._appearance.shadow_opacity) / 100.0)) * 255))
        )
        effect.setColor(shadow_color)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        border_width = int(self._appearance.border_width) if self._appearance.show_border else 0
        border_offset = border_width / 2.0
        paint_rect = QRectF(self.rect()).adjusted(border_offset, border_offset, -border_offset, -border_offset)
        radius = max(0, int(self._appearance.corner_radius))

        path = QPainterPath()
        path.addRoundedRect(paint_rect, radius, radius)
        painter.setClipPath(path)

        background_type = str(self._appearance.background_type or "solid").strip().lower()
        overall_opacity = int(self._appearance.background_opacity)
        base_color = apply_color_opacity(color_from_setting(self._appearance.background_color, "#5b5b5b"), overall_opacity)

        if background_type == "gradient":
            start_color = apply_color_opacity(
                color_from_setting(self._appearance.gradient_start_color, "#5b5b5b"),
                overall_opacity,
            )
            end_color = apply_color_opacity(
                color_from_setting(self._appearance.gradient_end_color, "#3f3f3f"),
                overall_opacity,
            )
            rect = paint_rect.toRect()
            direction = str(self._appearance.gradient_direction or "horizontal").strip().lower()
            if direction == "vertical":
                gradient = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            elif direction == "diagonal_down":
                gradient = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
            elif direction == "diagonal_up":
                gradient = QLinearGradient(rect.left(), rect.bottom(), rect.right(), rect.top())
            else:
                gradient = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
            gradient.setColorAt(0.0, start_color)
            gradient.setColorAt(1.0, end_color)
            painter.fillPath(path, gradient)
        else:
            painter.fillPath(path, base_color)

        if background_type == "image":
            source = self._blurred_pixmap if not self._blurred_pixmap.isNull() else self._background_pixmap
            if not source.isNull():
                painter.setOpacity(max(0.0, min(1.0, int(self._appearance.image_opacity) / 100.0)))
                _draw_aligned_pixmap(
                    painter,
                    paint_rect.toRect(),
                    source,
                    fit_mode=self._appearance.image_fit_mode,
                    alignment=self._appearance.image_alignment,
                )
                painter.setOpacity(1.0)

        overlay = apply_color_opacity(
            color_from_setting(self._appearance.overlay_tint, "#000000"),
            int(self._appearance.overlay_tint_opacity),
        )
        if overlay.alpha() > 0:
            painter.fillPath(path, overlay)

        painter.setClipping(False)
        if border_width > 0:
            pen = QPen(
                apply_color_opacity(
                    color_from_setting(self._appearance.border_color, "#ffffff"),
                    int(self._appearance.border_opacity),
                )
            )
            pen.setWidth(border_width)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)


class TopBarPanel(StyledPanel):
    def apply_settings(self, settings: TopBarBehaviorSettings) -> None:
        self.apply_panel_appearance(build_panel_appearance(settings, "appearance", fixed_height_key="height"))
