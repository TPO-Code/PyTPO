from __future__ import annotations

import unittest

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from TPOPyside.widgets.code_editor.editor import CodeEditor


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


def _build_folded_editor(*, trailing_lines: int) -> CodeEditor:
    editor = CodeEditor()
    editor.resize(480, 220)
    editor.file_path = "example.py"
    body = "\n".join(f"    value_{idx} = {idx}" for idx in range(40))
    trailing = "\n".join(f"line {idx}" for idx in range(trailing_lines))
    editor.setPlainText(f"def outer():\n{body}\n\n{trailing}")
    editor.show()
    _app().processEvents()
    editor._refresh_fold_ranges()
    editor._toggle_fold_at_block(0)
    _app().processEvents()
    return editor


def _select_fold_header(editor: CodeEditor) -> None:
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len("def outer():"), QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    _app().processEvents()


def _hidden_block_numbers(editor: CodeEditor) -> list[int]:
    hidden: list[int] = []
    block = editor.document().firstBlock()
    while block.isValid():
        if not block.isVisible():
            hidden.append(int(block.blockNumber()))
        block = block.next()
    return hidden


def _visible_block_numbers(editor: CodeEditor) -> list[int]:
    visible: list[int] = []
    block = editor.document().firstBlock()
    while block.isValid():
        if block.isVisible():
            visible.append(int(block.blockNumber()))
        block = block.next()
    return visible


class CodeEditorFoldEditTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_paste_over_collapsed_fold_clears_hidden_blocks_in_small_file(self) -> None:
        editor = _build_folded_editor(trailing_lines=300)
        _select_fold_header(editor)

        mime = QMimeData()
        mime.setText("def replacement():\n    pass\n")
        editor.insertFromMimeData(mime)
        _app().processEvents()

        self.assertEqual(editor.document().findBlockByNumber(0).text(), "def replacement():")
        self.assertTrue(editor.document().findBlockByNumber(0).isVisible())
        self.assertIn(0, editor._fold_ranges)
        self.assertNotIn(0, editor._folded_starts)
        self.assertEqual(_hidden_block_numbers(editor), [])

    def test_paste_over_collapsed_fold_forces_immediate_refresh_above_threshold(self) -> None:
        editor = _build_folded_editor(trailing_lines=13050)
        self.assertFalse(editor._automatic_fold_refresh_allowed())
        _select_fold_header(editor)

        mime = QMimeData()
        mime.setText("def replacement():\n    pass\n")
        editor.insertFromMimeData(mime)
        _app().processEvents()

        self.assertEqual(editor.document().findBlockByNumber(0).text(), "def replacement():")
        self.assertTrue(editor.document().findBlockByNumber(0).isVisible())
        self.assertIn(0, editor._fold_ranges)
        self.assertNotIn(0, editor._folded_starts)
        self.assertFalse(editor._fold_repair_immediate_pending)
        self.assertEqual(_hidden_block_numbers(editor), [])

    def test_collapsed_top_level_folds_shrink_scroll_range_and_scroll_safely(self) -> None:
        editor = CodeEditor()
        editor.resize(500, 240)
        editor.file_path = "example.py"
        parts: list[str] = []
        for fn in range(18):
            parts.append(f"def fn_{fn}():")
            for line in range(10):
                parts.append(f"    value_{fn}_{line} = {line}")
            parts.append("")
        editor.setPlainText("\n".join(parts))
        editor.show()
        _app().processEvents()
        editor._refresh_fold_ranges()
        for start_block in sorted(editor._fold_ranges):
            editor._toggle_fold_at_block(start_block)
        _app().processEvents()

        visible_blocks = _visible_block_numbers(editor)
        bar = editor.verticalScrollBar()
        self.assertEqual(bar.maximum(), max(0, len(visible_blocks) - int(bar.pageStep())))

        for value in range(bar.maximum() + 1):
            bar.setValue(value)
            _app().processEvents()
            block = editor.firstVisibleBlock()
            self.assertTrue(block.isValid())
            self.assertTrue(block.isVisible())

    def test_overview_strip_matches_scrollbar_geometry(self) -> None:
        editor = _build_folded_editor(trailing_lines=300)

        overview_geo = editor.overviewMarkerArea.geometry()
        viewport_geo = editor.viewport().geometry()

        self.assertEqual(overview_geo.top(), viewport_geo.top())
        self.assertEqual(overview_geo.height(), viewport_geo.height())
        self.assertEqual(overview_geo.left(), viewport_geo.right() + editor._theme_overview_gap())

    def test_overview_mapping_uses_visible_folded_lines(self) -> None:
        editor = _build_folded_editor(trailing_lines=300)

        visible_lines = editor._overview_visible_line_numbers()
        self.assertGreaterEqual(len(visible_lines), 2)
        self.assertEqual(visible_lines[0], 1)

        display_lines = editor._overview_display_lines({2, 10, visible_lines[1]}, visible_lines)

        self.assertIn(1, display_lines)
        self.assertIn(2, display_lines)
        self.assertEqual(len(display_lines), 2)


if __name__ == "__main__":
    unittest.main()
