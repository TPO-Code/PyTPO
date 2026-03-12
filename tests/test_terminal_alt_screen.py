from __future__ import annotations

import unittest

import pyte

from TPOPyside.widgets.terminal_widget import _TerminalByteStream, _TerminalScreenMux


def _row_text(screen, row: int) -> str:
    cols = int(getattr(screen, "columns", 0))
    cells = getattr(screen, "buffer", {}).get(int(row), {})
    return "".join(
        (cells.get(col).data if cells.get(col) and cells.get(col).data else " ")
        for col in range(cols)
    )


class TerminalAlternateScreenTests(unittest.TestCase):
    def _build(self) -> tuple[_TerminalScreenMux, pyte.ByteStream]:
        screen = _TerminalScreenMux(24, 8, history=200)
        stream = _TerminalByteStream(screen)
        return screen, stream

    def test_entering_1049_switches_to_blank_alternate_buffer(self) -> None:
        screen, stream = self._build()
        stream.feed(b"main-one\r\nmain-two\r\n")
        self.assertIn("main-one", _row_text(screen, 0))
        self.assertIn("main-two", _row_text(screen, 1))

        stream.feed(b"\x1b[?1049h")

        self.assertEqual(_row_text(screen, 0).strip(), "")
        self.assertEqual(_row_text(screen, 1).strip(), "")

    def test_leaving_1049_restores_main_buffer(self) -> None:
        screen, stream = self._build()
        stream.feed(b"main-one\r\nmain-two\r\n")
        stream.feed(b"\x1b[?1049h")
        stream.feed(b"alt-only\r\n")
        self.assertIn("alt-only", _row_text(screen, 0))

        stream.feed(b"\x1b[?1049l")

        self.assertIn("main-one", _row_text(screen, 0))
        self.assertIn("main-two", _row_text(screen, 1))

    def test_1049_and_text_in_same_chunk_draws_to_alternate_buffer(self) -> None:
        screen, stream = self._build()
        stream.feed(b"main-one\r\n")

        stream.feed(b"\x1b[?1049halt-inline\r\n")

        self.assertIn("alt-inline", _row_text(screen, 0))

    def test_private_mode_callback_handles_split_sequences(self) -> None:
        events: list[tuple[int, bool]] = []
        screen = _TerminalScreenMux(
            24,
            8,
            history=200,
            private_mode_callback=lambda mode, enabled: events.append((int(mode), bool(enabled))),
        )
        stream = pyte.ByteStream(screen)

        stream.feed(b"\x1b[?100")
        stream.feed(b"6h")
        stream.feed(b"\x1b[?1006")
        stream.feed(b"l")

        self.assertIn((1006, True), events)
        self.assertIn((1006, False), events)

    def test_csi_scroll_up_wipes_scrolled_region(self) -> None:
        screen = _TerminalScreenMux(12, 6, history=50)
        stream = _TerminalByteStream(screen)
        stream.feed(b"row1\r\nrow2\r\nrow3\r\nrow4\r\nrow5\r\n")

        # Mimic tmux clear strategy: set region and scroll it upward.
        stream.feed(b"\x1b[2;6r\x1b[5S")

        self.assertIn("row1", _row_text(screen, 0))
        self.assertEqual(_row_text(screen, 1).strip(), "")
        self.assertEqual(_row_text(screen, 2).strip(), "")
        self.assertEqual(_row_text(screen, 3).strip(), "")
        self.assertEqual(_row_text(screen, 4).strip(), "")
        self.assertEqual(_row_text(screen, 5).strip(), "")

    def test_private_restore_mode_sequence_does_not_break_stream(self) -> None:
        screen, stream = self._build()
        stream.feed(b"line1\r\nline2\r\n")
        stream.feed(b"\x1b[2;6r")
        before = getattr(screen, "margins", None)

        # Some TUIs emit private mode save/restore: CSI ? Pm s / CSI ? Pm r.
        stream.feed(b"\x1b[?1001s")
        stream.feed(b"\x1b[?1001r")
        stream.feed(b"after\r\n")

        after = getattr(screen, "margins", None)
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertEqual(int(getattr(before, "top", 0)), int(getattr(after, "top", 0)))
        self.assertEqual(int(getattr(before, "bottom", 0)), int(getattr(after, "bottom", 0)))
        rows = [_row_text(screen, idx) for idx in range(int(getattr(screen, "lines", 0)))]
        self.assertTrue(any("after" in row for row in rows))

    def test_private_mode_save_restore_round_trip(self) -> None:
        events: list[tuple[int, bool]] = []
        screen = _TerminalScreenMux(
            24,
            8,
            history=200,
            private_mode_callback=lambda mode, enabled: events.append((int(mode), bool(enabled))),
        )
        stream = _TerminalByteStream(screen)

        stream.feed(b"\x1b[?1002h")
        stream.feed(b"\x1b[?1002s")
        stream.feed(b"\x1b[?1002l")
        stream.feed(b"\x1b[?1002r")

        self.assertIn((1002, True), events)
        self.assertIn((1002, False), events)
        self.assertEqual(events[-1], (1002, True))


if __name__ == "__main__":
    unittest.main()
