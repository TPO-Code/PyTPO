from __future__ import annotations

from functools import lru_cache

from pytpo.services.asset_paths import preferred_shared_asset_path
from pytpo.services.theme_compiler import STRUCTURED_THEME_EXTENSION, compile_qsst_file_with_tokens


@lru_cache(maxsize=1)
def load_default_stylesheet() -> str:
    for relative_path in ("themes/Default.qsst", "themes/Default.qss"):
        theme_path = preferred_shared_asset_path(relative_path)
        if not theme_path.is_file():
            continue
        if theme_path.suffix.lower() == STRUCTURED_THEME_EXTENSION:
            return compile_qsst_file_with_tokens(theme_path).stylesheet
        return theme_path.read_text(encoding="utf-8")
    return ""
