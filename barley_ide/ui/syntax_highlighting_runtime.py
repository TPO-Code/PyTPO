from __future__ import annotations

from typing import Any

from TPOPyside.widgets.code_editor.syntax_highlighters import set_syntax_color_resolver
from barley_ide.services.syntax_highlighting_config import build_syntax_color_resolver


def configure_syntax_highlighting_runtime(manager: Any) -> None:
    raw = manager.get("editor.syntax_highlighting", scope_preference="ide", default={})
    resolver = build_syntax_color_resolver(raw)
    set_syntax_color_resolver(resolver)

