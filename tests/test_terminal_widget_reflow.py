from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from TPOPyside.widgets.terminal_widget import TerminalWidget, _TerminalByteStream


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class TerminalWidgetReflowTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _make_terminal(self, *, cols: int, rows: int = 8) -> TerminalWidget:
        term = TerminalWidget.__new__(TerminalWidget)
        term._history_limit = 5000
        term._view_offset = 0
        term._cursor_visible = True
        term._display_rows_cache_cols = -1
        term._display_rows_cache = []
        term._private_modes_enabled = set()
        term._mouse_mode_btn = False
        term._mouse_mode_any = False
        term._mouse_mode_sgr = False
        term._bracket_paste_enabled = False
        term._sel_start = None
        term._sel_end = None
        term._on_private_mode_changed = lambda *args, **kwargs: None
        term._screen = term._create_screen(cols, rows)
        term._stream = _TerminalByteStream(term._screen)
        term._virt_cols = cols
        term._virt_rows = rows
        term._visible_cols = lambda: cols
        term._visible_rows = lambda: rows
        term._invalidate_display_rows_cache()
        return term

    def test_wrapped_output_reflows_when_terminal_gets_wider(self) -> None:
        term = self._make_terminal(cols=10)
        term._stream.feed(b"notifications.py\r\n")
        term._invalidate_display_rows_cache()

        narrow_rows = [row["display_text"] for row in term._visible_row_records() if row["display_text"]]
        self.assertEqual(narrow_rows[:2], ["notificati", "ons.py"])

        term._screen.resize(lines=8, columns=20)
        term._visible_cols = lambda: 20
        term._virt_cols = 20
        term._invalidate_display_rows_cache()

        wide_rows = [row["display_text"] for row in term._visible_row_records() if row["display_text"]]
        self.assertEqual(wide_rows[:1], ["notifications.py"])

    def test_wrapped_traceback_rows_keep_full_source_line(self) -> None:
        term = self._make_terminal(cols=18)
        payload = b'File "/tmp/example/notifications.py", line 12, in main\r\n'
        term._stream.feed(payload)
        term._invalidate_display_rows_cache()

        target = term._traceback_target_from_row(0)
        self.assertEqual(target, ("/tmp/example/notifications.py", 12, 1))

    def test_copy_selection_uses_logical_line_after_reflow(self) -> None:
        app = _app()
        term = self._make_terminal(cols=10)
        term._stream.feed(b"notifications.py\r\n")
        term._screen.resize(lines=8, columns=20)
        term._visible_cols = lambda: 20
        term._virt_cols = 20
        term._invalidate_display_rows_cache()

        term._sel_start = (0, 0)
        term._sel_end = (len("notifications.py") - 1, 0)
        term.copySelection()

        self.assertEqual(app.clipboard().text(), "notifications.py")

    def test_cursor_remains_visible_at_end_of_non_empty_line(self) -> None:
        term = self._make_terminal(cols=20)
        term._stream.feed(b"prompt> ")
        term._invalidate_display_rows_cache()

        rows = [row for row in term._visible_row_records() if row["display_text"]]
        self.assertEqual(rows[:1][0]["display_text"], "prompt>")
        self.assertEqual(rows[:1][0]["cursor_col"], len("prompt> "))

    def test_selection_stays_attached_to_buffer_when_view_scrolls(self) -> None:
        term = self._make_terminal(cols=20, rows=3)
        term._stream.feed(b"line0\r\nline1\r\nline2\r\nline3\r\nline4\r\n")
        term._invalidate_display_rows_cache()

        term._sel_start = (0, 1)
        term._sel_end = (4, 2)
        term._view_offset = 1
        term.copySelection()

        self.assertEqual(_app().clipboard().text(), "line1\nline2")

    def test_selection_can_copy_across_offscreen_history(self) -> None:
        term = self._make_terminal(cols=20, rows=2)
        term._stream.feed(b"line0\r\nline1\r\nline2\r\nline3\r\nline4\r\n")
        term._invalidate_display_rows_cache()

        term._sel_start = (0, 0)
        term._sel_end = (4, 4)
        term.copySelection()

        self.assertEqual(_app().clipboard().text(), "line0\nline1\nline2\nline3\nline4")


if __name__ == "__main__":
    unittest.main()
