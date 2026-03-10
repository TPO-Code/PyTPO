"""Reusable PySide widgets shared across projects."""

__all__ = ["CodeEditor", "Window", "CustomTitleBar", "TDocDocumentWidget"]


def __getattr__(name: str):
    if name == "CodeEditor":
        from .code_editor import CodeEditor

        return CodeEditor
    if name == "Window":
        from .custom_window import Window

        return Window
    if name == "CustomTitleBar":
        from .custom_window import CustomTitleBar

        return CustomTitleBar
    if name == "TDocDocumentWidget":
        from .tdoc_support import TDocDocumentWidget

        return TDocDocumentWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
