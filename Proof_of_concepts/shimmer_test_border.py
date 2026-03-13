import sys
import math

from PySide6.QtCore import QEasingCurve, QVariantAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QTextEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
)


class ShimmerFrame(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._shimmer_enabled = False
        self._shimmer_progress = 0.0
        self._travel_angle_degrees = -25.0

        self._border_radius = 10.0
        self._border_width = 2.0
        self._padding = 6

        self.editor = QTextEdit(self)
        self.editor.setPlainText(
            "Border-only shimmer demo.\n\n"
            "This version paints on a wrapper widget, which is much safer than\n"
            "trying to paint on QTextEdit's outer scroll-area shell."
        )
        self.editor.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #e6e6e6;
                border: none;
                padding: 8px;
                selection-background-color: #4a6fa5;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            self._padding, self._padding, self._padding, self._padding
        )
        layout.addWidget(self.editor)

        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(1800)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.Linear)
        self.animation.valueChanged.connect(self._on_value_changed)

        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def _on_value_changed(self, value):
        self._shimmer_progress = float(value)
        self.update()

    def set_shimmer_enabled(self, enabled: bool):
        self._shimmer_enabled = enabled
        if enabled:
            self.animation.start()
        else:
            self.animation.stop()
            self.update()

    def shimmer_enabled(self) -> bool:
        return self._shimmer_enabled

    def set_travel_angle(self, degrees: float):
        self._travel_angle_degrees = degrees
        self.update()

    def text_edit(self) -> QTextEdit:
        return self.editor

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(
            self._border_width / 2.0,
            self._border_width / 2.0,
            -self._border_width / 2.0,
            -self._border_width / 2.0,
        )

        path = QPainterPath()
        path.addRoundedRect(rect, self._border_radius, self._border_radius)

        # Base border
        base_pen = QPen(QColor("#4a4a4a"))
        base_pen.setWidthF(self._border_width)
        painter.setPen(base_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if not self._shimmer_enabled:
            return

        angle_rad = math.radians(self._travel_angle_degrees)
        dx = math.cos(angle_rad)
        dy = math.sin(angle_rad)

        diagonal = math.hypot(rect.width(), rect.height())
        travel_distance = diagonal * 2.2
        offset = (self._shimmer_progress - 0.5) * travel_distance

        cx = rect.center().x() + dx * offset
        cy = rect.center().y() + dy * offset

        half_len = diagonal
        x1 = cx - dx * half_len
        y1 = cy - dy * half_len
        x2 = cx + dx * half_len
        y2 = cy + dy * half_len

        gradient = QLinearGradient(x1, y1, x2, y2)
        gradient.setColorAt(0.00, QColor(255, 255, 255, 0))
        gradient.setColorAt(0.42, QColor(120, 180, 255, 0))
        gradient.setColorAt(0.48, QColor(120, 180, 255, 60))
        gradient.setColorAt(0.50, QColor(180, 220, 255, 180))
        gradient.setColorAt(0.52, QColor(120, 180, 255, 60))
        gradient.setColorAt(0.58, QColor(120, 180, 255, 0))
        gradient.setColorAt(1.00, QColor(255, 255, 255, 0))

        shimmer_pen = QPen()
        shimmer_pen.setBrush(gradient)
        shimmer_pen.setWidthF(self._border_width + 0.8)

        painter.setPen(shimmer_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Border Shimmer Demo")
        self.resize(760, 420)

        self.editor_frame = ShimmerFrame()
        self.button = QPushButton("Start shimmer")
        self.button.clicked.connect(self.toggle_shimmer)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor_frame)
        layout.addWidget(self.button)

        self.setStyleSheet("""
            QWidget {
                background-color: #161616;
            }
            QPushButton {
                background-color: #2b2b2b;
                color: #e6e6e6;
                border: 1px solid #4a4a4a;
                border-radius: 6px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                background-color: #353535;
            }
            QPushButton:pressed {
                background-color: #252525;
            }
        """)

    def toggle_shimmer(self):
        enabled = not self.editor_frame.shimmer_enabled()
        self.editor_frame.set_shimmer_enabled(enabled)
        self.button.setText("Stop shimmer" if enabled else "Start shimmer")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    sys.exit(app.exec())