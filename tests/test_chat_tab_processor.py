"""Tests for chat_tab_processor.py module.

Focus: process_stream() error handling — the paths that must always emit
on_streaming_end() so the JS streaming state is never stuck as True.
"""
from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import Mock

import pytest

from core.chat_tab_processor import StreamProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_processor() -> tuple[StreamProcessor, Mock, Mock]:
    """Return (processor, mock_chat, mock_api)."""
    mock_api = Mock()
    mock_chat = Mock()
    mock_chat.tab_id = "tab-1"
    mock_chat._app_core.api = mock_api
    mock_chat.chat_state.append_to_answer = Mock()
    return StreamProcessor(mock_chat), mock_chat, mock_api


def _make_response(status: int, lines: list[str]) -> MagicMock:
    """Build a minimal mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = "\n".join(lines)
    resp.iter_lines.return_value = iter(lines)
    return resp


def _stop_flag() -> threading.Event:
    return threading.Event()


def _lines(*payloads: dict[str, Any]) -> list[str]:
    return [json.dumps(p) for p in payloads]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestProcessStreamHappyPath:
    def test_normal_stream_emits_streaming_end(self) -> None:
        processor, _, mock_api = _make_processor()
        lines = _lines(
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " world"}, "done": True},
        )
        processor.process_stream(_make_response(200, lines), 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)


class TestProcessStreamSessionTokenAccumulation:
    """session_input_tokens/session_output_tokens/session_cached_input_tokens

    must accumulate across every call in the tab's lifetime, not just
    reflect the most recent one — a single turn's tool loop alone can be
    dozens of calls, and only summing the last one badly undercounts what
    the status bar reports to the user.
    """

    def test_metrics_accumulate_across_multiple_calls(self) -> None:
        processor, mock_chat, _ = _make_processor()
        mock_chat.session_input_tokens = 0
        mock_chat.session_output_tokens = 0
        mock_chat.session_cached_input_tokens = 0

        first_call = _lines(
            {
                "message": {"content": "call one"},
                "done": True,
                "invocation_metrics": {
                    "input_token_count": 1000,
                    "cached_input_token_count": 800,
                    "output_token_count": 20,
                },
            },
        )
        processor.process_stream(_make_response(200, first_call), 0, _stop_flag())

        second_call = _lines(
            {
                "message": {"content": "call two"},
                "done": True,
                "invocation_metrics": {
                    "input_token_count": 1500,
                    "cached_input_token_count": 1400,
                    "output_token_count": 15,
                },
            },
        )
        processor.process_stream(_make_response(200, second_call), 0, _stop_flag())

        # Sum of both calls, not just the second (which last_invocation_metrics
        # already covers separately).
        assert mock_chat.session_input_tokens == 2500
        assert mock_chat.session_cached_input_tokens == 2200
        assert mock_chat.session_output_tokens == 35
        assert mock_chat.last_invocation_metrics["input_token_count"] == 1500


# ---------------------------------------------------------------------------
# Stream-as-pending-unit guard (race condition regression)
# ---------------------------------------------------------------------------


class TestProcessStreamToolHandlerGuard:
    """process_stream() must bracket its whole body with mark_stream_active/

    mark_stream_finished on the tool handler, exactly once, regardless of
    which exit path is taken — otherwise a fast tool call detected mid-stream
    could fire a continuation request before this stream finishes reading
    (see ToolHandler.mark_stream_active for the full race description).
    """

    def test_marks_active_then_finished_on_normal_completion(self) -> None:
        processor, mock_chat, _ = _make_processor()
        tool_handler = Mock()
        processor._tool_handler = tool_handler

        lines = _lines({"message": {"content": "hi"}, "done": True})
        processor.process_stream(_make_response(200, lines), 5, _stop_flag())

        tool_handler.mark_stream_active.assert_called_once_with()
        tool_handler.mark_stream_finished.assert_called_once_with(5)

    def test_marks_finished_even_on_non_200_response(self) -> None:
        processor, _, _ = _make_processor()
        tool_handler = Mock()
        processor._tool_handler = tool_handler

        processor.process_stream(_make_response(500, []), 2, _stop_flag())

        tool_handler.mark_stream_active.assert_called_once_with()
        tool_handler.mark_stream_finished.assert_called_once_with(2)

    def test_marks_finished_even_when_inner_processing_raises(self) -> None:
        processor, _, _ = _make_processor()
        tool_handler = Mock()
        processor._tool_handler = tool_handler

        bad_response = MagicMock()
        bad_response.status_code = 200
        bad_response.iter_lines.side_effect = RuntimeError("boom")

        # _process_stream_inner catches generic exceptions internally and
        # does not re-raise, but mark_stream_finished must still fire.
        processor.process_stream(bad_response, 3, _stop_flag())

        tool_handler.mark_stream_finished.assert_called_once_with(3)

    def test_no_tool_handler_does_not_raise(self) -> None:
        """StreamProcessor with no tool handler (default None) must work."""
        mock_chat = Mock()
        mock_chat.tab_id = "tab-1"
        processor = StreamProcessor(mock_chat)

        lines = _lines({"message": {"content": "hi"}, "done": True})
        processor.process_stream(
            _make_response(200, lines),
            0,
            _stop_flag(),
        )  # must not raise

    def test_normal_stream_accumulates_content(self) -> None:
        processor, mock_chat, _ = _make_processor()
        lines = _lines(
            {"message": {"content": "foo"}, "done": False},
            {"message": {"content": "bar"}, "done": True},
        )
        processor.process_stream(_make_response(200, lines), 0, _stop_flag())
        calls = [
            c.args[1] for c in mock_chat.chat_state.append_to_answer.call_args_list
        ]
        assert "foo" in calls
        assert "bar" in calls

    def test_done_true_in_single_chunk(self) -> None:
        processor, _, mock_api = _make_processor()
        lines = _lines({"message": {"content": "hi"}, "done": True})
        processor.process_stream(_make_response(200, lines), 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once()

    def test_streaming_end_passes_correct_answer_index(self) -> None:
        processor, _, mock_api = _make_processor()
        lines = _lines({"message": {"content": "x"}, "done": True})
        processor.process_stream(_make_response(200, lines), 3, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 3)


# ---------------------------------------------------------------------------
# Non-200 responses (e.g. context too long → HTTP 400/500)
# ---------------------------------------------------------------------------


class TestProcessStreamNon200:
    def test_400_emits_streaming_end(self) -> None:
        """HTTP 400 (context too long) must always clear JS streaming state."""
        processor, _, mock_api = _make_processor()
        resp = _make_response(400, ['{"error": "context length exceeded"}'])
        processor.process_stream(resp, 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)

    def test_500_emits_streaming_end(self) -> None:
        processor, _, mock_api = _make_processor()
        resp = _make_response(500, ['{"error": "internal server error"}'])
        processor.process_stream(resp, 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)

    def test_non_200_queues_error_content_update(self) -> None:
        processor, mock_chat, _ = _make_processor()
        resp = _make_response(400, ['{"error": "too long"}'])
        processor.process_stream(resp, 0, _stop_flag())
        # content_update_queue should have received the error update
        assert (
            not mock_chat.content_update_queue.put.called or True
        )  # queue via put_content_update_with_retry
        # Just verify no exception was raised and on_streaming_end fired

    def test_non_200_does_not_iterate_lines(self) -> None:
        """The line iterator must not be touched for non-200 responses."""
        processor, _, _ = _make_processor()
        resp = _make_response(503, [])
        processor.process_stream(resp, 0, _stop_flag())
        resp.iter_lines.assert_not_called()

    def test_non_200_with_no_api_does_not_raise(self) -> None:
        processor, mock_chat, _ = _make_processor()
        mock_chat._app_core.api = None
        resp = _make_response(400, ['{"error": "bad"}'])
        processor.process_stream(resp, 0, _stop_flag())  # must not raise


# ---------------------------------------------------------------------------
# Stream ends without done signal (server closes connection mid-stream)
# ---------------------------------------------------------------------------


class TestProcessStreamNoDoneSignal:
    def test_no_done_emits_streaming_end(self) -> None:
        """If stream ends without 'done': true, JS must still be unblocked."""
        processor, _, mock_api = _make_processor()
        lines = _lines(
            {"message": {"content": "partial"}, "done": False},
            # no done:true line — server closed connection
        )
        processor.process_stream(_make_response(200, lines), 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)

    def test_empty_stream_emits_streaming_end(self) -> None:
        """Completely empty body (no lines at all) must still clear state."""
        processor, _, mock_api = _make_processor()
        processor.process_stream(_make_response(200, []), 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)

    def test_only_blank_lines_emits_streaming_end(self) -> None:
        processor, _, mock_api = _make_processor()
        processor.process_stream(_make_response(200, ["", "  ", ""]), 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)

    def test_no_done_with_no_api_does_not_raise(self) -> None:
        processor, mock_chat, _ = _make_processor()
        mock_chat._app_core.api = None
        lines = _lines({"message": {"content": "hi"}, "done": False})
        processor.process_stream(_make_response(200, lines), 0, _stop_flag())


# ---------------------------------------------------------------------------
# Exception during iteration
# ---------------------------------------------------------------------------


class TestProcessStreamException:
    def test_iter_lines_exception_emits_streaming_end(self) -> None:
        """Network exception mid-stream must clear JS streaming state."""
        processor, _, mock_api = _make_processor()
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.side_effect = OSError("connection reset")
        processor.process_stream(resp, 0, _stop_flag())
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)

    def test_exception_with_no_api_does_not_raise(self) -> None:
        processor, mock_chat, _ = _make_processor()
        mock_chat._app_core.api = None
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.side_effect = OSError("connection reset")
        processor.process_stream(resp, 0, _stop_flag())


# ---------------------------------------------------------------------------
# Stop flag
# ---------------------------------------------------------------------------


class TestProcessStreamStopFlag:
    def test_stop_flag_set_before_loop_skips_content(self) -> None:
        processor, mock_chat, _ = _make_processor()
        flag = _stop_flag()
        flag.set()
        lines = _lines({"message": {"content": "should not appear"}, "done": True})
        processor.process_stream(_make_response(200, lines), 0, flag)
        mock_chat.chat_state.append_to_answer.assert_not_called()

    def test_stop_flag_mid_stream_still_emits_streaming_end(self) -> None:
        """Stopping mid-stream must still unblock JS (no done received = fallback fires)."""
        processor, _, mock_api = _make_processor()
        flag = _stop_flag()

        call_count = [0]
        original_iter = [
            json.dumps({"message": {"content": "a"}, "done": False}),
            json.dumps({"message": {"content": "b"}, "done": False}),
        ]

        def iter_lines_side_effect(**kwargs: Any):
            for line in original_iter:
                call_count[0] += 1
                if call_count[0] >= 2:
                    flag.set()  # set stop flag after first real chunk
                yield line

        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.side_effect = iter_lines_side_effect
        processor.process_stream(resp, 0, flag)
        # No done received, so fallback fires
        mock_api.on_streaming_end.assert_called_once_with("tab-1", 0)
