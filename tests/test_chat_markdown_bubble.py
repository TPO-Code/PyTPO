from __future__ import annotations

import unittest

from barley_ide.ui.widgets.chat_markdown_bubble import ChatMarkdownBubble


class ChatMarkdownBubbleMarkdownTests(unittest.TestCase):
    def test_inserts_gap_before_unordered_list(self) -> None:
        html = ChatMarkdownBubble._render_markdown_html("Here are items:\n- one\n- two")
        self.assertIn("<ul>", html)
        self.assertIn("<p>one</p>", html)
        self.assertIn("<p>two</p>", html)

    def test_inserts_gap_before_star_list(self) -> None:
        html = ChatMarkdownBubble._render_markdown_html("Things:\n* alpha\n* beta")
        self.assertIn("<ul>", html)
        self.assertIn("<p>alpha</p>", html)
        self.assertIn("<p>beta</p>", html)

    def test_inserts_gap_after_list_before_paragraph(self) -> None:
        html = ChatMarkdownBubble._render_markdown_html(
            "Steps:\n1. first\n2. second\nDone."
        )
        self.assertIn("<ol>", html)
        self.assertIn("<p>first</p>", html)
        self.assertIn("<p>second</p>", html)
        self.assertIn("<p>Done.</p>", html)

    def test_preserves_list_markers_inside_fenced_code(self) -> None:
        html = ChatMarkdownBubble._render_markdown_html(
            "```text\n- literal\n1. literal\n```"
        )
        self.assertNotIn("<ul>", html)
        self.assertNotIn("<ol>", html)
        self.assertIn("- literal", html)
        self.assertIn("1. literal", html)


class ChatMarkdownBubbleDiffTests(unittest.TestCase):
    @staticmethod
    def _relative_display(path_text: str) -> str:
        return str(path_text).removeprefix("/repo/")

    def test_diff_summary_formats_absolute_path_for_display(self) -> None:
        label, added, removed = ChatMarkdownBubble._extract_diff_summary(
            "*** Update File: /repo/src/example.py\n- old\n+ new\n",
            diff_path_display=self._relative_display,
        )
        self.assertEqual(label, "src/example.py")
        self.assertEqual(added, 1)
        self.assertEqual(removed, 1)

    def test_render_diff_html_formats_diff_headers_for_display(self) -> None:
        html = ChatMarkdownBubble._render_diff_html(
            "\n".join(
                [
                    "diff --git a//repo/src/example.py b//repo/src/example.py",
                    "--- a//repo/src/example.py",
                    "+++ b//repo/src/example.py",
                ]
            ),
            diff_path_display=self._relative_display,
        )
        self.assertIn("diff --git a/src/example.py b/src/example.py", html)
        self.assertIn("--- a/src/example.py", html)
        self.assertIn("+++ b/src/example.py", html)
        self.assertNotIn("/repo/src/example.py", html)


if __name__ == "__main__":
    unittest.main()
