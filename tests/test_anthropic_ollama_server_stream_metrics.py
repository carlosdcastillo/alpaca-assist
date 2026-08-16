"""Regression coverage for _process_stream's token accounting.

Before this fix, invocation_metrics required BOTH input_tokens and
output_tokens to have arrived cleanly via specific streaming events — any
mid-stream failure (network hiccup, provider dropping the connection) or a
provider not sending the expected terminal usage event meant the whole
call's tokens silently vanished from session_output_tokens/
session_input_tokens, even though the provider still billed for them.
"""
from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock

from anthropic_ollama_server import OllamaRequestHandler


def _make_handler() -> OllamaRequestHandler:
    """A handler instance with just enough stubbed to exercise

    _process_stream without a real socket/HTTP request.

    send_response/send_header/end_headers/_send_text_chunk are real
    BaseHTTPRequestHandler methods, so setattr (not plain attribute
    assignment) is what mypy accepts for stubbing them per-instance --
    plain assignment is flagged as reassigning a method, not an attribute.
    """
    handler = object.__new__(OllamaRequestHandler)
    handler.wfile = io.BytesIO()
    for name in ("send_response", "send_header", "end_headers"):
        setattr(handler, name, MagicMock())
    return handler


def _last_chunk(handler: OllamaRequestHandler) -> dict[str, Any]:
    wfile = handler.wfile
    assert isinstance(wfile, io.BytesIO)
    lines = [l for l in wfile.getvalue().decode().splitlines() if l]
    result: dict[str, Any] = json.loads(lines[-1])
    return result


def _message_start(input_tokens=100, cache_write=0, cache_read=0) -> dict:
    return {
        "type": "message_start",
        "message": {
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_write,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def _text_delta(text: str) -> dict:
    return {"type": "content_block_delta", "delta": {"text": text}}


def _message_delta(stop_reason="end_turn", output_tokens=50) -> dict:
    return {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason},
        "usage": {"output_tokens": output_tokens},
    }


class TestCleanStreamUnaffected:
    def test_clean_stream_reports_exact_metrics(self) -> None:
        handler = _make_handler()
        events = [
            _message_start(input_tokens=100),
            _text_delta("hello world"),
            _message_delta(output_tokens=50),
        ]

        handler._process_stream(iter(events))

        chunk = _last_chunk(handler)
        assert chunk["invocation_metrics"]["input_token_count"] == 100
        assert chunk["invocation_metrics"]["output_token_count"] == 50
        assert chunk["done_reason"] == "end_turn"


class TestMidStreamFailureStillReportsMetrics:
    def test_exception_mid_stream_still_sends_estimated_metrics(self) -> None:
        """The dominant real-world failure: message_start arrives (so we

        know input_tokens), a chunk of real text streams in (so real
        output was generated and billed), then the connection dies before
        the terminal message_delta usage event ever arrives.
        """

        def flaky_stream():
            yield _message_start(input_tokens=100)
            yield _text_delta("a" * 400)  # ~100 estimated tokens at chars/4
            raise ConnectionError("connection reset by peer")

        handler = _make_handler()

        handler._process_stream(flaky_stream())

        chunk = _last_chunk(handler)
        assert chunk["done_reason"] == "error"
        assert chunk["error"] == "connection reset by peer"
        metrics = chunk["invocation_metrics"]
        assert metrics["input_token_count"] == 100
        # Estimated from the 400 chars actually received, not silently zero.
        assert metrics["output_token_count"] == 100

    def test_exception_before_any_content_reports_no_metrics_but_does_not_crash(
        self,
    ) -> None:
        def flaky_stream():
            raise ConnectionError("connection reset by peer")
            yield  # pragma: no cover - makes this a generator

        handler = _make_handler()

        handler._process_stream(flaky_stream())

        chunk = _last_chunk(handler)
        assert chunk["done_reason"] == "error"
        assert chunk["error"] == "connection reset by peer"
        assert "invocation_metrics" not in chunk


class TestMissingTerminalUsageEventStillReportsMetrics:
    def test_clean_completion_without_message_delta_usage_estimates_output(
        self,
    ) -> None:
        """A provider quirk (e.g. Fireworks not sending the expected

        terminal usage block for some response shapes) must not zero out
        an otherwise-successful call's accounting.
        """
        handler = _make_handler()
        events = [
            _message_start(input_tokens=100),
            _text_delta("a" * 200),
            # No message_delta event at all — stream just ends.
        ]

        handler._process_stream(iter(events))

        chunk = _last_chunk(handler)
        assert chunk["done_reason"] == "stop"
        metrics = chunk["invocation_metrics"]
        assert metrics["input_token_count"] == 100
        assert metrics["output_token_count"] == 50  # 200 chars / 4


class TestEmptyStreamReportsNoMetrics:
    def test_no_content_and_no_usage_reports_no_metrics(self) -> None:
        handler = _make_handler()

        handler._process_stream(iter([]))

        chunk = _last_chunk(handler)
        assert "invocation_metrics" not in chunk


class TestLoopBodyFailureStillReportsAnError:
    """_events_tolerating_mid_stream_failure only catches exceptions from
    the *upstream generator* (network hiccups, a provider dropping the
    connection). It never covered a failure in this handler's own
    per-event processing -- self._send_text_chunk, self._wrap_tool_call,
    a print() call, anything -- and do_POST's outer except Exception is
    not a working safety net for that case either, since by the time this
    loop runs, send_response(200) and end_headers() already fired, so
    send_error(500, ...) assumes a fresh response and can itself fail
    silently. Confirmed by reading the code: any bug in this loop body,
    known or not-yet-discovered, previously had no working path to the
    client at all -- just silence until the client's own read-timeout
    fired, indistinguishable from the server still legitimately working.
    """

    def test_exception_in_send_text_chunk_still_completes_the_response(self) -> None:
        handler = _make_handler()
        setattr(
            handler,
            "_send_text_chunk",
            MagicMock(
                side_effect=UnicodeEncodeError(
                    "charmap",
                    "\u26a0",
                    0,
                    1,
                    "character maps to <undefined>",
                ),
            ),
        )
        events = [
            _message_start(input_tokens=100),
            _text_delta("hello"),
        ]

        handler._process_stream(iter(events))  # must not raise

        chunk = _last_chunk(handler)
        assert chunk["done"] is True
        assert chunk["done_reason"] == "error"
        assert chunk["error"] is not None

    def test_exception_partway_through_the_loop_still_completes_the_response(
        self,
    ) -> None:
        """A failure on event N must not lose accounting for events before
        it, matching the existing generator-level-failure guarantee.
        """
        handler = _make_handler()
        real_send_text_chunk = handler._send_text_chunk
        call_count = {"n": 0}

        def flaky_send_text_chunk(text: str, count: int) -> None:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated bug in chunk delivery")
            real_send_text_chunk(text, count)

        setattr(handler, "_send_text_chunk", flaky_send_text_chunk)
        events = [
            _message_start(input_tokens=100),
            _text_delta("first chunk landed fine"),
            _text_delta("second chunk triggers the bug"),
        ]

        handler._process_stream(iter(events))  # must not raise

        chunk = _last_chunk(handler)
        assert chunk["done_reason"] == "error"
        assert "simulated bug in chunk delivery" in chunk["error"]
        assert chunk["invocation_metrics"]["input_token_count"] == 100
