import sys
import math

from PySide6.QtCore import QEasingCurve, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QPushButton, QTextEdit, QVBoxLayout, QWidget


class ShimmerTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._shimmer_enabled = False
        self._shimmer_progress = 0.0

        # This is the DIRECTION THE SHIMMER MOVES.
        # The bright band itself will appear perpendicular to this.
        self._travel_angle_degrees = -30.0

        self.setPlainText(
            "This shimmer should now move properly.\n\n"
            "Click the button to toggle it."
        )

        self.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #e6e6e6;
                border: 1px solid #4a4a4a;
                border-radius: 8px;
                padding: 8px;
                selection-background-color: #4a6fa5;
            }
        """)

        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(1800)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.Linear)
        self.animation.valueChanged.connect(self._on_value_changed)

    def _on_value_changed(self, value):
        self._shimmer_progress = float(value)
        self.viewport().update()

    def set_shimmer_enabled(self, enabled: bool):
        self._shimmer_enabled = enabled
        if enabled:
            self.animation.start()
        else:
            self.animation.stop()
            self.viewport().update()

    def shimmer_enabled(self) -> bool:
        return self._shimmer_enabled

    def set_travel_angle(self, degrees: float):
        self._travel_angle_degrees = degrees
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)

        if not self._shimmer_enabled:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.viewport().rect()

        angle_rad = math.radians(self._travel_angle_degrees)
        dx = math.cos(angle_rad)
        dy = math.sin(angle_rad)

        diagonal = math.hypot(rect.width(), rect.height())

        # Move the gradient axis fully offscreen to fully offscreen.
        travel_distance = diagonal * 2.0
        offset = (self._shimmer_progress - 0.5) * travel_distance

        cx = rect.center().x() + dx * offset
        cy = rect.center().y() + dy * offset

        # Long enough line so the gradient covers the whole viewport.
        half_len = diagonal

        x1 = cx - dx * half_len
        y1 = cy - dy * half_len
        x2 = cx + dx * half_len
        y2 = cy + dy * half_len

        gradient = QLinearGradient(x1, y1, x2, y2)

        # Transparent edges, bright narrow center
        gradient.setColorAt(0.00, QColor(255, 255, 255, 0))
        gradient.setColorAt(0.42, QColor(120, 180, 255, 0))
        gradient.setColorAt(0.48, QColor(120, 180, 255, 18))
        gradient.setColorAt(0.50, QColor(180, 220, 255, 70))
        gradient.setColorAt(0.52, QColor(120, 180, 255, 18))
        gradient.setColorAt(0.58, QColor(120, 180, 255, 0))
        gradient.setColorAt(1.00, QColor(255, 255, 255, 0))

        painter.fillRect(rect, gradient)

        pen = QPen(QColor(120, 180, 255, 65))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 8, 8)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Working Shimmer Demo")
        self.resize(700, 400)

        self.editor = ShimmerTextEdit()
        self.editor.set_travel_angle(-25)

        self.button = QPushButton("Start shimmer")
        self.button.clicked.connect(self.toggle_shimmer)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor)
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
        enabled = not self.editor.shimmer_enabled()
        self.editor.set_shimmer_enabled(enabled)
        self.button.setText("Stop shimmer" if enabled else "Start shimmer")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    sys.exit(app.exec())