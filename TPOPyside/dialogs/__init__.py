__all__ = ["DialogWindow", "ColorPickerDialog"]


def __getattr__(name: str):
    if name == "DialogWindow":
        from .custom_dialog import DialogWindow

        return DialogWindow
    if name == "ColorPickerDialog":
        from .color_picker_dialog import ColorPickerDialog

        return ColorPickerDialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
