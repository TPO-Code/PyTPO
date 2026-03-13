from __future__ import annotations

import unittest

from pytpo.ui.widgets.chat_markdown_bubble import ChatMarkdownBubble


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


if __name__ == "__main__":
    unittest.main()
