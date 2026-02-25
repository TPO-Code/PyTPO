from PySide6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox



from TPOPyside.widgets.color_picker import ColorPicker
class ColorPickerDialog(QDialog):
    def __init__(self, initial_color, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Color")
        self.resize(600, 350)

        # Layout
        layout = QVBoxLayout(self)

        # Add your custom ColorPicker widget
        self.picker_widget = ColorPicker(initial_color)
        layout.addWidget(self.picker_widget)

        # Add Standard OK/Cancel Buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def get_color(self):
        return self.picker_widget.getColor()