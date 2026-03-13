"""Image viewer tab widget with crisp nearest-neighbor rendering."""

from __future__ import annotations

import math
import os
import uuid
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class _ImageGraphicsView(QGraphicsView):
    zoomChanged = Signal(float, bool)

    MIN_SCALE = 0.01
    MAX_SCALE = 128.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fit_mode = True
        self._middle_zoom_active = False
        self._middle_zoom_last_pos = QPoint()

        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setAlignment(Qt.AlignCenter)
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

    def is_fit_mode(self) -> bool:
        return self._fit_mode

    def current_zoom_percent(self) -> float:
        return float(self.transform().m11()) * 100.0

    def fit_to_scene(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        rect = scene.sceneRect()
        if rect.isNull() or rect.isEmpty():
            return
        self._fit_mode = True
        self.resetTransform()
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.zoomChanged.emit(self.current_zoom_percent(), True)

    def reset_zoom(self) -> None:
        self._fit_mode = False
        self.resetTransform()
        self.zoomChanged.emit(self.current_zoom_percent(), False)

    def set_zoom_percent(self, percent: float, *, anchor: QPoint | None = None) -> None:
        target_scale = max(self.MIN_SCALE, min(self.MAX_SCALE, float(percent) / 100.0))
        current_scale = max(self.MIN_SCALE, float(self.transform().m11()))
        factor = target_scale / current_scale
        self._apply_zoom_factor(factor, anchor=anchor)

    def _apply_zoom_factor(self, factor: float, *, anchor: QPoint | None = None) -> None:
        if not math.isfinite(factor) or factor <= 0.0:
            return

        current_scale = max(self.MIN_SCALE, float(self.transform().m11()))
        target_scale = max(self.MIN_SCALE, min(self.MAX_SCALE, current_scale * factor))
        actual_factor = target_scale / current_scale
        if abs(actual_factor - 1.0) < 1e-6:
            return

        self._fit_mode = False
        old_scene_pos = self.mapToScene(anchor) if isinstance(anchor, QPoint) else None
        self.scale(actual_factor, actual_factor)

        if old_scene_pos is not None:
            new_scene_pos = self.mapToScene(anchor)
            delta = new_scene_pos - old_scene_pos
            self.translate(delta.x(), delta.y())

        self.zoomChanged.emit(self.current_zoom_percent(), False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_to_scene()

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            super().wheelEvent(event)
            return
        factor = 1.15 if event.angleDelta().y() > 0 else (1.0 / 1.15)
        self._apply_zoom_factor(factor, anchor=event.position().toPoint())
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._middle_zoom_active = True
            self._middle_zoom_last_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.SizeVerCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._middle_zoom_active:
            current_pos = event.position().toPoint()
            delta_y = int(current_pos.y() - self._middle_zoom_last_pos.y())
            if delta_y != 0:
                factor = math.pow(1.01, -delta_y)
                self._apply_zoom_factor(factor, anchor=current_pos)
                self._middle_zoom_last_pos = current_pos
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton and self._middle_zoom_active:
            self._middle_zoom_active = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ImageViewerWidget(QWidget):
    def __init__(self, *, file_path: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("PyTPOImageViewer")
        self.editor_id = str(uuid.uuid4())
        self.file_path: str | None = None
        self._background_color = QColor()

        self._scene = QGraphicsScene(self)
        self._pixmap_item = QGraphicsPixmapItem()
        self._pixmap_item.setTransformationMode(Qt.FastTransformation)
        self._scene.addItem(self._pixmap_item)

        self._view = _ImageGraphicsView(self)
        self._view.setScene(self._scene)
        self._view.zoomChanged.connect(self._refresh_status_text)

        self._status_label = QLabel(self)
        self._status_label.setObjectName("PyTPOImageViewerStatus")
        self._status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status_label.setText("No image loaded")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._view, 1)
        layout.addWidget(self._status_label, 0)
        self.set_viewer_background("")

        if file_path:
            self.load_file(file_path)

    def display_name(self) -> str:
        return os.path.basename(self.file_path) if self.file_path else "Image"

    def set_file_path(self, path: str | None) -> None:
        clean = str(path).strip() if isinstance(path, str) and path.strip() else None
        self.file_path = str(Path(clean).resolve()) if clean else None

    def fit_to_view(self) -> None:
        self._view.fit_to_scene()

    def reset_zoom(self) -> None:
        self._view.reset_zoom()

    def zoom_100(self) -> None:
        self._view.set_zoom_percent(100.0)

    def set_viewer_background(self, value: str | QColor | None) -> None:
        color = QColor(value) if isinstance(value, QColor) else QColor(str(value or "").strip())
        if not color.isValid():
            color = QColor(self.palette().color(self.backgroundRole()))
        if not color.isValid():
            color = QColor("#252526")
        if color == self._background_color:
            return
        self._background_color = QColor(color)
        bg_hex = self._background_color.name()
        self._view.setBackgroundBrush(self._background_color)
        self._scene.setBackgroundBrush(self._background_color)
        self.setStyleSheet(
            f"#PyTPOImageViewer{{background:{bg_hex};}}"
            f"#PyTPOImageViewerStatus{{background:{bg_hex};}}"
        )

    def load_file(self, path: str) -> bool:
        target = str(path or "").strip()
        if not target:
            return False
        cpath = str(Path(target).resolve())
        if not os.path.exists(cpath):
            return False

        reader = QImageReader(cpath)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return False

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return False

        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self.set_file_path(cpath)
        self.fit_to_view()
        self._refresh_status_text()
        return True

    def _refresh_status_text(self, *_args) -> None:
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull() or not self.file_path:
            self._status_label.setText("No image loaded")
            return
        width = int(pixmap.width())
        height = int(pixmap.height())
        zoom = int(round(self._view.current_zoom_percent()))
        mode = "fit" if self._view.is_fit_mode() else "custom"
        self._status_label.setText(f"{width}x{height} px | {zoom}% | {mode}")
