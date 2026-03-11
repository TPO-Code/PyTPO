__all__ = [
    "DialogWindow",
    "ColorPickerDialog",
    "SchemaSettingsDialog",
    "SettingsSchema",
    "SchemaPage",
    "SchemaSection",
    "SchemaField",
    "FieldBinding",
    "SettingsBackend",
]


def __getattr__(name: str):
    if name == "DialogWindow":
        from .custom_dialog import DialogWindow

        return DialogWindow
    if name == "ColorPickerDialog":
        from .color_picker_dialog import ColorPickerDialog

        return ColorPickerDialog
    if name in {
        "SchemaSettingsDialog",
        "SettingsSchema",
        "SchemaPage",
        "SchemaSection",
        "SchemaField",
        "FieldBinding",
        "SettingsBackend",
    }:
        from .schema_settings_dialog import (
            FieldBinding,
            SchemaField,
            SchemaPage,
            SchemaSection,
            SchemaSettingsDialog,
            SettingsBackend,
            SettingsSchema,
        )

        exports = {
            "SchemaSettingsDialog": SchemaSettingsDialog,
            "SettingsSchema": SettingsSchema,
            "SchemaPage": SchemaPage,
            "SchemaSection": SchemaSection,
            "SchemaField": SchemaField,
            "FieldBinding": FieldBinding,
            "SettingsBackend": SettingsBackend,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
