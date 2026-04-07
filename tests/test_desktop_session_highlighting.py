from barley_ide.services.language_id import language_id_for_path
from TPOPyside.widgets.code_editor.syntax_highlighters import (
    LANGUAGE_HIGHLIGHTER_MAP,
    SYNTAX_LANGUAGE_LABELS,
)
from TPOPyside.widgets.code_editor.keypress_handlers import get_language_id


def test_desktop_and_session_paths_resolve_to_desktop_language() -> None:
    assert language_id_for_path("/tmp/example.desktop") == "desktop"
    assert language_id_for_path("/tmp/example.session") == "desktop"
    assert get_language_id("/tmp/example.desktop") == "desktop"
    assert get_language_id("/tmp/example.session") == "desktop"


def test_desktop_language_has_shared_highlighter_registration() -> None:
    assert "desktop" in LANGUAGE_HIGHLIGHTER_MAP
    assert "desktop" in SYNTAX_LANGUAGE_LABELS
