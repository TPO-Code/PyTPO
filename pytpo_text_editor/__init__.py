from __future__ import annotations

from typing import TYPE_CHECKING

from .app import main

if TYPE_CHECKING:
    from .main_window import TextEditorWindow

def __getattr__(name: str):
    if name == "TextEditorWindow":
        from .main_window import TextEditorWindow

        return TextEditorWindow
    raise AttributeError(name)


__all__ = ["TextEditorWindow", "main"]
