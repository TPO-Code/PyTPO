"""Reusable PySide widgets shared across projects."""

__all__ = [
    "CodeEditor",
    "Window",
    "CustomTitleBar",
    "TDocDocumentWidget",
    "SplitterTabWorkspace",
    "WorkspaceTabs",
    "DropZone",
]


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
    if name == "SplitterTabWorkspace":
        from .split_tab_workspace import SplitterTabWorkspace

        return SplitterTabWorkspace
    if name == "WorkspaceTabs":
        from .split_tab_workspace import WorkspaceTabs

        return WorkspaceTabs
    if name == "DropZone":
        from .split_tab_workspace import DropZone

        return DropZone
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
