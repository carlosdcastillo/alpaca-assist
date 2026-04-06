"""Regression coverage for input-token capture in OllamaRequestHandler._process_stream.

Fireworks's Anthropic-compatible streaming API sends a placeholder
input_tokens=0 in the message_start event and only reports the real count
later, in message_delta — unlike Anthropic's own API, which reports the
correct value in message_start already (and repeats it in message_delta).
Capturing input_tokens only from message_start, as the original code did,
showed "in:0" in the status bar for every Fireworks model.
"""
from __future__ import annotations

from unittest.mock import Mock

from anthropic_ollama_server import OllamaRequestHandler


def _make_handler() -> OllamaRequestHandler:
    """Build an OllamaRequestHandler without going through BaseHTTPRequestHandler's

    socket-binding __init__ — _process_stream only touches send_response,
    send_header, end_headers, and wfile (via the _send_* helpers), so those
    are the only attributes that need stubbing.
    """
    handler = OllamaRequestHandler.__new__(OllamaRequestHandler)
    handler.send_response = Mock()  # type: ignore[method-assign]
    handler.send_header = Mock()  # type: ignore[method-assign]
    handler.end_headers = Mock()  # type: ignore[method-assign]
    handler.wfile = Mock()
    return handler


def _capture_metrics(handler: OllamaRequestHandler, events: list[dict]) -> dict | None:
    captured: dict = {}

    def fake_send_completion_chunk(
        count,
        stop_reason="stop",
        tool_calls=None,
        invocation_metrics=None,
    ):
        captured["metrics"] = invocation_metrics

    handler._send_completion_chunk = fake_send_completion_chunk  # type: ignore[method-assign]
    handler._process_stream(events)
    return captured.get("metrics")


class TestInputTokenCapture:
    def test_fireworks_style_uses_message_delta_for_input_tokens(self) -> None:
        """Fireworks: message_start.usage.input_tokens is a 0 placeholder;

        the real value only appears in message_delta.usage."""
        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 0, "output_tokens": 0}},
            },
            {"type": "content_block_delta", "delta": {"text": "hi"}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ]

        metrics = _capture_metrics(_make_handler(), events)

        assert metrics is not None
        assert metrics["input_token_count"] == 10
        assert metrics["output_token_count"] == 5

    def test_anthropic_style_input_tokens_already_correct_at_start(self) -> None:
        """Anthropic: message_start already has the real input_tokens, and

        message_delta repeats the same value — must not regress this."""
        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 16, "output_tokens": 1}},
            },
            {"type": "content_block_delta", "delta": {"text": "hi"}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"input_tokens": 16, "output_tokens": 8},
            },
        ]

        metrics = _capture_metrics(_make_handler(), events)

        assert metrics is not None
        assert metrics["input_token_count"] == 16
        assert metrics["output_token_count"] == 8

    def test_message_delta_without_input_tokens_keeps_message_start_value(
        self,
    ) -> None:
        """If message_delta's usage block doesn't include input_tokens (only

        output_tokens), the value already captured from message_start must
        be preserved, not silently reset to None."""
        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 16, "output_tokens": 1}},
            },
            {"type": "content_block_delta", "delta": {"text": "hi"}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 8},  # no input_tokens key here
            },
        ]

        metrics = _capture_metrics(_make_handler(), events)

        assert metrics is not None
        assert metrics["input_token_count"] == 16
        assert metrics["output_token_count"] == 8
