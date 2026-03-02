from PySide6.QtWidgets import QDialogButtonBox, QVBoxLayout, QWidget

from src.ui.custom_dialog import DialogWindow


class ColorPickerDialog(DialogWindow):
    def __init__(self, initial_color, parent=None, use_native_chrome: bool = False):
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Select Color")
        self.resize(600, 350)

        host = QWidget(self)
        self.set_content_widget(host)
        layout = QVBoxLayout(host)

        from TPOPyside.widgets.color_picker import ColorPicker

        self.picker_widget = ColorPicker(initial_color)
        layout.addWidget(self.picker_widget)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=host)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def get_color(self):
        return self.picker_widget.getColor()
