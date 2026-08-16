"""Tests for url_to_markdown.convert_html_to_markdown's response parsing.

Regression coverage for a KeyError('text') crash: some models (e.g.
glm-5p2 via Fireworks) emit a leading "thinking" content block with no
"text" key before the actual answer block, so content[0] isn't reliably
the text block.
"""

from __future__ import annotations

from unittest.mock import Mock
from unittest.mock import patch

from url_to_markdown import convert_html_to_markdown


def _patched_client(response: dict):
    mock_client = Mock()
    mock_client.complete.return_value = response
    return patch(
        "url_to_markdown.get_llm_client",
        return_value=(mock_client, "fake-model"),
    )


class TestConvertHtmlToMarkdown:
    def test_extracts_text_when_thinking_block_precedes_it(self) -> None:
        """The exact glm-5p2 shape: thinking block has no "text" key at all."""
        response = {
            "content": [
                {"type": "thinking", "thinking": "reasoning...", "signature": ""},
                {"type": "text", "text": "# Hello\n\nConverted markdown."},
            ],
        }
        with _patched_client(response):
            result = convert_html_to_markdown("<h1>Hello</h1>")

        assert result == "# Hello\n\nConverted markdown."

    def test_extracts_text_with_no_thinking_block(self) -> None:
        """Plain models (e.g. Claude without extended thinking) — content[0]

        is already the text block; must keep working unchanged."""
        response = {"content": [{"type": "text", "text": "Plain result."}]}
        with _patched_client(response):
            result = convert_html_to_markdown("<p>x</p>")

        assert result == "Plain result."

    def test_concatenates_multiple_text_blocks(self) -> None:
        response = {
            "content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": "Part one."},
                {"type": "text", "text": "Part two."},
            ],
        }
        with _patched_client(response):
            result = convert_html_to_markdown("<p>x</p>")

        assert result == "Part one.\nPart two."

    def test_no_text_block_returns_empty_string(self) -> None:
        response = {"content": [{"type": "thinking", "thinking": "only this"}]}
        with _patched_client(response):
            result = convert_html_to_markdown("<p>x</p>")

        assert result == ""
