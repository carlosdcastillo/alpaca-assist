"""Tests for _build_anthropic_messages in anthropic_ollama_server.py.

This function had no dedicated test coverage before — every model call
passes through it, and it's the one place that decides whether a tool
result carrying an image (see image_tool_result.py) actually becomes a
real image content block for the model, or gets sent as inert marker
text. Covers both that new behavior and a baseline for the existing
tool_use_call / tool_result / user-images handling so a regression in
either doesn't slip through silently.
"""
from __future__ import annotations

import base64
import json

from anthropic_ollama_server import _build_anthropic_messages
from image_tool_result import encode_image_result
from video_tool_result import encode_video_result


class TestToolUseCall:
    def test_becomes_assistant_tool_use_block(self) -> None:
        messages = [
            {
                "role": "tool_use_call",
                "call": {"id": "t1", "name": "internal_read_file", "arguments": {"file_path": "x.py"}},
            },
        ]
        result, errors = _build_anthropic_messages(messages)
        assert errors == []
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        block = result[0]["content"][0]
        assert block["type"] == "tool_use"
        assert block["id"] == "t1"
        assert block["name"] == "internal_read_file"
        assert block["input"] == {"file_path": "x.py"}

    def test_cache_control_applied_when_requested(self) -> None:
        messages = [
            {
                "role": "tool_use_call",
                "cache_control": True,
                "call": {"id": "t1", "name": "x", "arguments": {}},
            },
        ]
        result, _errors = _build_anthropic_messages(messages)
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


class TestToolResultPlainText:
    def test_becomes_user_tool_result_text_block(self) -> None:
        messages = [{"role": "tool_result", "id": "t1", "content": "some output"}]
        result, _errors = _build_anthropic_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        block = result[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "t1"
        assert block["content"] == [{"type": "text", "text": "some output"}]

    def test_cache_control_applied_when_requested(self) -> None:
        messages = [{"role": "tool_result", "id": "t1", "content": "x", "cache_control": True}]
        result, _errors = _build_anthropic_messages(messages)
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


class TestToolResultWithImage:
    """The actual new behavior: a tool_result whose stored content embeds
    an image_tool_result sentinel must expand into a real image content
    block, not be sent as raw marker text.
    """

    def test_expands_to_image_and_text_blocks(self) -> None:
        encoded = encode_image_result("image/png", "QUJDRA==", "a screenshot")
        messages = [{"role": "tool_result", "id": "t1", "content": encoded}]
        result, _errors = _build_anthropic_messages(messages)

        assert len(result) == 1
        block = result[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "t1"

        inner = block["content"]
        assert len(inner) == 2
        assert inner[0]["type"] == "image"
        assert inner[0]["source"] == {
            "type": "base64",
            "media_type": "image/png",
            "data": "QUJDRA==",
        }
        assert inner[1] == {"type": "text", "text": "a screenshot"}

    def test_works_with_the_prepend_prefix_replay_adds(self) -> None:
        """prepare_continuation_messages prepends 'Tool execution result:\\n'
        before the stored content — the sentinel must still be found.
        """
        encoded = encode_image_result("image/jpeg", "Zm9v", "desc")
        messages = [
            {"role": "tool_result", "id": "t1", "content": f"Tool execution result:\n{encoded}"},
        ]
        result, _errors = _build_anthropic_messages(messages)
        inner = result[0]["content"][0]["content"]
        assert inner[0]["type"] == "image"
        assert inner[0]["source"]["media_type"] == "image/jpeg"

    def test_cache_control_still_applies_alongside_image(self) -> None:
        encoded = encode_image_result("image/png", "QUJD", "d")
        messages = [
            {"role": "tool_result", "id": "t1", "content": encoded, "cache_control": True},
        ]
        result, _errors = _build_anthropic_messages(messages)
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


class TestToolResultWithVideo:
    def test_sends_description_not_locator_or_video_bytes_to_model(self) -> None:
        encoded = encode_video_result("video/webm", "/tmp/demo.webm", 1234, "demo ready")
        stored = '{"content": [{"type": "text", "text": ' + json.dumps(encoded) + "}]}"
        messages = [{"role": "tool_result", "id": "v1", "content": stored}]

        result, _errors = _build_anthropic_messages(messages)

        content = result[0]["content"][0]["content"]
        assert content == [{"type": "text", "text": "demo ready"}]
        assert "ALPACA_VIDEO_RESULT" not in str(content)


class TestUserWithImages:
    def test_valid_image_becomes_image_block(self) -> None:
        # Minimal valid PNG magic bytes + arbitrary trailing data.
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        b64 = base64.b64encode(png_bytes).decode("ascii")
        messages = [{"role": "user", "content": "look at this", "images": [b64]}]
        result, errors = _build_anthropic_messages(messages)
        assert errors == []
        blocks = result[0]["content"]
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["media_type"] == "image/png"
        assert blocks[-1] == {"type": "text", "text": "look at this"}

    def test_unrecognized_image_format_reports_error_not_crash(self) -> None:
        b64 = base64.b64encode(b"not a real image").decode("ascii")
        messages = [{"role": "user", "content": "x", "images": [b64]}]
        result, errors = _build_anthropic_messages(messages)
        assert len(errors) == 1
        assert "Unknown image format" in errors[0]
        # No image block was added for the unrecognized one.
        assert all(b["type"] != "image" for b in result[0]["content"])


class TestPlainMessages:
    def test_plain_user_message_passes_through(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        result, _errors = _build_anthropic_messages(messages)
        assert result == [{"role": "user", "content": "hello"}]

    def test_does_not_mutate_input(self) -> None:
        original = [{"role": "user", "content": "hello"}]
        snapshot = [dict(m) for m in original]
        _build_anthropic_messages(original)
        assert original == snapshot
