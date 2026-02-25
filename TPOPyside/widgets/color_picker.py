import sys
from PySide6.QtCore import Qt, Signal, QRegularExpression
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPen, QRegularExpressionValidator, QMouseEvent, QPaintEvent
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSpinBox, QLineEdit, QSizePolicy

class ColorSquare(QWidget):
    """
    The 2D area allowing selection of Saturation (x-axis) and Value (y-axis).
    """
    colorChanged = Signal(QColor)

    def __init__(self, color=QColor("red")):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(50, 50)
        self._hue = color.hue() / 360.0 if color.hue() >= 0 else 0
        self._sat = color.saturationF()
        self._val = color.valueF()

    def setHue(self, hue_0_to_1):
        self._hue = hue_0_to_1
        self.update()

    def setSatVal(self, sat, val):
        self._sat = sat
        self._val = val
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()

        # 1. Base Hue Background
        # We actually construct this using gradients to simulate mixing

        # Horizontal Gradient: White to Pure Hue
        h_grad = QLinearGradient(0, 0, rect.width(), 0)
        h_grad.setColorAt(0, Qt.white)
        h_grad.setColorAt(1, QColor.fromHsvF(self._hue, 1, 1))
        painter.fillRect(rect, h_grad)

        # Vertical Gradient: Transparent to Black (Value/Darkness)
        v_grad = QLinearGradient(0, 0, 0, rect.height())
        v_grad.setColorAt(0, Qt.transparent)
        v_grad.setColorAt(1, Qt.black)
        painter.fillRect(rect, v_grad)

        # Draw Selector Circle
        x = int(self._sat * rect.width())
        y = int((1 - self._val) * rect.height())

        # Contrast ring for visibility
        painter.setPen(QPen(Qt.black if self._val > 0.5 else Qt.white, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(x - 5, y - 5, 10, 10)

    def _handle_mouse(self, event: QMouseEvent):
        x = max(0, min(event.position().x(), self.width()))
        y = max(0, min(event.position().y(), self.height()))

        self._sat = x / self.width()
        self._val = 1.0 - (y / self.height())

        # Reconstruct color using stored hue
        color = QColor.fromHsvF(self._hue, self._sat, self._val)
        self.colorChanged.emit(color)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._handle_mouse(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._handle_mouse(event)


class ColorBar(QWidget):
    """
    Generic bar for Hue (Rainbow) or Alpha (Gradient).
    Orientation: Vertical.
    """
    valueChanged = Signal(float)  # 0.0 to 1.0

    def __init__(self, is_hue=True):
        super().__init__()
        self._is_hue = is_hue
        self._value = 0.0 if is_hue else 1.0 # Hue 0, Alpha 1
        self._base_color = QColor("red") # Used for Alpha gradient
        self.setFixedWidth(24)
        self.setMinimumHeight(50)

    def setValue(self, val):
        self._value = max(0.0, min(1.0, val))
        self.update()

    def setBaseColor(self, color):
        """Only relevant for Alpha bar to show gradient of current color"""
        self._base_color = color
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        rect = self.rect()

        # Draw Checkerboard if Alpha
        if not self._is_hue:
            check_size = 6
            painter.setPen(Qt.NoPen)
            for y in range(0, rect.height(), check_size):
                for x in range(0, rect.width(), check_size):
                    if (x // check_size + y // check_size) % 2 == 0:
                        painter.setBrush(Qt.lightGray)
                    else:
                        painter.setBrush(Qt.white)
                    painter.drawRect(x, y, check_size, check_size)

        # Draw Gradient
        gradient = QLinearGradient(0, 0, 0, rect.height())

        if self._is_hue:
            # Rainbow
            gradient.setColorAt(0.0, QColor(255, 0, 0))
            gradient.setColorAt(0.16, QColor(255, 255, 0))
            gradient.setColorAt(0.33, QColor(0, 255, 0))
            gradient.setColorAt(0.5, QColor(0, 255, 255))
            gradient.setColorAt(0.66, QColor(0, 0, 255))
            gradient.setColorAt(0.83, QColor(255, 0, 255))
            gradient.setColorAt(1.0, QColor(255, 0, 0))
        else:
            # Alpha: Opaque Color -> Transparent
            c_opaque = QColor(self._base_color)
            c_opaque.setAlpha(255)
            c_trans = QColor(self._base_color)
            c_trans.setAlpha(0)
            gradient.setColorAt(0, c_opaque)
            gradient.setColorAt(1, c_trans)

        painter.fillRect(rect, gradient)

        # Draw Indicator
        y = int(self._value * rect.height()) if self._is_hue else int((1 - self._value) * rect.height())

        # Simple triangle arrows or a line
        painter.setPen(QPen(Qt.black, 2))
        painter.drawLine(0, y, rect.width(), y)
        painter.setPen(QPen(Qt.white, 2))
        painter.drawLine(0, y+1, rect.width(), y+1)

    def _handle_mouse(self, event):
        y = max(0, min(event.position().y(), self.height()))
        val = y / self.height()

        if self._is_hue:
            self._value = val
        else:
            self._value = 1.0 - val # Alpha is usually 1 at top, 0 at bottom

        self.valueChanged.emit(self._value)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._handle_mouse(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._handle_mouse(event)


class ColorPreview(QWidget):
    """Displays the selected color with a checkerboard background for alpha."""
    def __init__(self, color=QColor("white")):
        super().__init__()
        self._color = color
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setColor(self, color):
        self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()

        # Draw checkerboard
        check_size = 10
        painter.setPen(Qt.NoPen)
        for y in range(0, rect.height(), check_size):
            for x in range(0, rect.width(), check_size):
                if (x // check_size + y // check_size) % 2 == 0:
                    painter.setBrush(Qt.lightGray)
                else:
                    painter.setBrush(Qt.white)
                painter.drawRect(x, y, check_size, check_size)

        # Draw Color
        painter.setBrush(self._color)
        painter.drawRect(rect)


class ColorPicker(QWidget):
    colorChanged = Signal(QColor)

    def __init__(self, initial_color=QColor("red")):
        super().__init__()
        self.setWindowTitle("Color Picker")

        # Internal state
        self._color = initial_color

        # --- UI Components ---

        # 1. Visual Pickers
        self.sat_val_square = ColorSquare(self._color)
        self.hue_bar = ColorBar(is_hue=True)
        self.alpha_bar = ColorBar(is_hue=False)

        # 2. Preview
        self.preview_area = ColorPreview(self._color)

        # 3. Inputs
        self.spin_r = self._create_spinbox(255)
        self.spin_g = self._create_spinbox(255)
        self.spin_b = self._create_spinbox(255)
        self.spin_a = self._create_spinbox(255)

        self.spin_h = self._create_spinbox(359)
        self.spin_s = self._create_spinbox(255)
        self.spin_v = self._create_spinbox(255)

        self.edit_hex = QLineEdit()
        # Regex for Hex (#RRGGBB or #AARRGGBB)
        reg_ex = QRegularExpression("^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
        validator = QRegularExpressionValidator(reg_ex, self.edit_hex)
        self.edit_hex.setValidator(validator)

        # --- Layouts ---

        main_layout = QHBoxLayout(self)

        # Left: Square + Bars
        visual_layout = QHBoxLayout()
        visual_layout.addWidget(self.sat_val_square)
        visual_layout.addWidget(self.hue_bar)
        visual_layout.addWidget(self.alpha_bar)

        # Right: Controls
        control_layout = QVBoxLayout()
        control_layout.setContentsMargins(10, 0, 0, 0)

        # Preview
        control_layout.addWidget(QLabel("Preview:"))
        control_layout.addWidget(self.preview_area)
        control_layout.addSpacing(10)

        # RGB Inputs
        rgb_layout = QGridLayout()
        rgb_layout.addWidget(QLabel("R:"), 0, 0)
        rgb_layout.addWidget(self.spin_r, 0, 1)
        rgb_layout.addWidget(QLabel("G:"), 1, 0)
        rgb_layout.addWidget(self.spin_g, 1, 1)
        rgb_layout.addWidget(QLabel("B:"), 2, 0)
        rgb_layout.addWidget(self.spin_b, 2, 1)
        rgb_layout.addWidget(QLabel("Alpha:"), 3, 0)
        rgb_layout.addWidget(self.spin_a, 3, 1)

        rgb_group = QWidget()
        rgb_group.setLayout(rgb_layout)
        control_layout.addWidget(rgb_group)
        control_layout.addSpacing(10)

        # HSV Inputs
        hsv_layout = QGridLayout()
        hsv_layout.addWidget(QLabel("H:"), 0, 0)
        hsv_layout.addWidget(self.spin_h, 0, 1)
        hsv_layout.addWidget(QLabel("S:"), 1, 0)
        hsv_layout.addWidget(self.spin_s, 1, 1)
        hsv_layout.addWidget(QLabel("V:"), 2, 0)
        hsv_layout.addWidget(self.spin_v, 2, 1)

        hsv_group = QWidget()
        hsv_group.setLayout(hsv_layout)
        control_layout.addWidget(hsv_group)
        control_layout.addSpacing(10)

        # Hex Input
        hex_layout = QHBoxLayout()
        hex_layout.addWidget(QLabel("Hex:"))
        hex_layout.addWidget(self.edit_hex)
        control_layout.addLayout(hex_layout)

        control_layout.addStretch()

        main_layout.addLayout(visual_layout, 2)
        main_layout.addLayout(control_layout, 1)

        # --- Connections ---

        # Visual Widgets
        self.sat_val_square.colorChanged.connect(self._on_sat_val_changed)
        self.hue_bar.valueChanged.connect(self._on_hue_slider_changed)
        self.alpha_bar.valueChanged.connect(self._on_alpha_slider_changed)

        # Spinboxes (RGB)
        self.spin_r.valueChanged.connect(self._on_rgb_changed)
        self.spin_g.valueChanged.connect(self._on_rgb_changed)
        self.spin_b.valueChanged.connect(self._on_rgb_changed)
        self.spin_a.valueChanged.connect(self._on_rgb_changed)

        # Spinboxes (HSV)
        self.spin_h.valueChanged.connect(self._on_hsv_input_changed)
        self.spin_s.valueChanged.connect(self._on_hsv_input_changed)
        self.spin_v.valueChanged.connect(self._on_hsv_input_changed)

        # Hex
        self.edit_hex.editingFinished.connect(self._on_hex_changed)

        # Initialization
        self._update_ui()

    def _create_spinbox(self, max_val):
        sb = QSpinBox()
        sb.setRange(0, max_val)
        return sb

    def getColor(self):
        return self._color

    def setColor(self, color):
        if self._color != color:
            self._color = color
            self._update_ui()
            self.colorChanged.emit(self._color)

    # --- Internal Logic ---

    def _update_ui(self):
        """Update all UI elements to reflect self._color without triggering signals loop."""
        # Block signals to prevent recursive updates
        widgets = [
            self.sat_val_square, self.hue_bar, self.alpha_bar,
            self.spin_r, self.spin_g, self.spin_b, self.spin_a,
            self.spin_h, self.spin_s, self.spin_v, self.edit_hex
        ]
        for w in widgets:
            w.blockSignals(True)

        try:
            # 1. Update Square
            h = self._color.hue()
            # QColor.hue() returns -1 for achromatic (greyscale), default to 0 (red)
            if h == -1: h = 0
            self.sat_val_square.setHue(h / 360.0)
            self.sat_val_square.setSatVal(self._color.saturationF(), self._color.valueF())

            # 2. Update Bars
            self.hue_bar.setValue(h / 360.0)
            self.alpha_bar.setBaseColor(self._color)
            self.alpha_bar.setValue(self._color.alphaF())

            # 3. Update Preview
            self.preview_area.setColor(self._color)

            # 4. Update RGB
            self.spin_r.setValue(self._color.red())
            self.spin_g.setValue(self._color.green())
            self.spin_b.setValue(self._color.blue())
            self.spin_a.setValue(self._color.alpha())

            # 5. Update HSV
            self.spin_h.setValue(h)
            self.spin_s.setValue(self._color.saturation())
            self.spin_v.setValue(self._color.value())

            # 6. Update Hex
            # Standard hex strings usually don't have alpha unless specified
            # We will use #AARRGGBB format if alpha < 255
            if self._color.alpha() < 255:
                hex_str = self._color.name(QColor.HexArgb)
            else:
                hex_str = self._color.name(QColor.HexRgb)
            self.edit_hex.setText(hex_str.upper())

        finally:
            for w in widgets:
                w.blockSignals(False)

    # --- Slots ---

    def _on_sat_val_changed(self, color_from_square):
        # The square gives us a color with correct S, V, and H.
        # But it doesn't know about current Alpha (defaults to 255).
        # We must apply the current alpha from our state.
        new_color = QColor(color_from_square)
        new_color.setAlpha(self._color.alpha())
        self.setColor(new_color)

    def _on_hue_slider_changed(self, val):
        # val is already 0.0 to 1.0
        h = val
        s = self._color.saturationF()
        v = self._color.valueF()
        a = self._color.alphaF()
        # fromHsvF expects h, s, v, a to all be 0.0-1.0
        self.setColor(QColor.fromHsvF(h, s, v, a))

    def _on_alpha_slider_changed(self, val):
        new_color = QColor(self._color)
        new_color.setAlphaF(val)
        self.setColor(new_color)

    def _on_rgb_changed(self):
        r = self.spin_r.value()
        g = self.spin_g.value()
        b = self.spin_b.value()
        a = self.spin_a.value()
        self.setColor(QColor(r, g, b, a))

    def _on_hsv_input_changed(self):
        h = self.spin_h.value()
        s = self.spin_s.value()
        v = self.spin_v.value()
        # Keep current alpha
        a = self.spin_a.value()
        c = QColor.fromHsv(h, s, v)
        c.setAlpha(a)
        self.setColor(c)

    def _on_hex_changed(self):
        text = self.edit_hex.text()
        if not text.startswith('#'):
            text = '#' + text

        c = QColor(text)
        if c.isValid():
            self.setColor(c)
        else:
            # Revert to current valid color if invalid hex
            self._update_ui()

if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = ColorPicker(QColor(0, 120, 255, 200)) # Start with a semi-transparent blue
    window.resize(600, 350)
    window.show()

    sys.exit(app.exec())