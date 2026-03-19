from __future__ import annotations

import unittest

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from TPOPyside.widgets.code_editor.editor import CodeEditor
from TPOPyside.widgets.tdoc_core import TDocEditorWidget


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


def _select_range(widget, start: int, end: int) -> None:
    cursor = widget.textCursor()
    cursor.setPosition(int(start))
    cursor.setPosition(int(end), QTextCursor.KeepAnchor)
    widget.setTextCursor(cursor)


class OccurrenceHighlightingTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_code_editor_highlights_exact_multiline_selection(self) -> None:
        editor = CodeEditor()
        editor.setPlainText("alpha\nbeta\n\nalpha\nbeta\n")

        _select_range(editor, 0, len("alpha\nbeta"))
        editor._refresh_occurrence_markers()

        self.assertEqual(editor._overview_occurrence_term, "alpha\nbeta")
        self.assertEqual(len(editor._occurrence_highlight_selections), 2)
        self.assertEqual(editor._overview_occurrence_lines, {1, 2, 4, 5})

    def test_code_editor_falls_back_to_word_under_caret_without_selection(self) -> None:
        editor = CodeEditor()
        editor.setPlainText("alpha beta alpha")

        cursor = editor.textCursor()
        cursor.setPosition(1)
        editor.setTextCursor(cursor)
        editor._refresh_occurrence_markers()

        self.assertEqual(editor._overview_occurrence_term, "alpha")
        self.assertEqual(len(editor._occurrence_highlight_selections), 2)
        self.assertEqual(editor._overview_occurrence_lines, {1})

    def test_tdoc_editor_highlights_exact_multiline_selection(self) -> None:
        editor = TDocEditorWidget()
        editor.setPlainText("alpha\nbeta\n\nalpha\nbeta\n")

        _select_range(editor, 0, len("alpha\nbeta"))
        editor._refresh_occurrence_markers()

        self.assertEqual(editor._overview_occurrence_term, "alpha\nbeta")
        self.assertEqual(len(editor._occurrence_highlight_selections), 2)
        self.assertEqual(editor._overview_occurrence_lines, {1, 2, 4, 5})


if __name__ == "__main__":
    unittest.main()
