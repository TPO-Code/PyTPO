from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main_window import TextEditorWindow


def main() -> int:
    from .main_window import main as _main

    return _main()


def __getattr__(name: str):
    if name == "TextEditorWindow":
        from .main_window import TextEditorWindow

        return TextEditorWindow
    raise AttributeError(name)


__all__ = ["TextEditorWindow", "main"]
