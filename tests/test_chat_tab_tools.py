"""Tests for chat_tab_tools.py module.

This module tests the ToolHandler class with minimal mocking where possible.
Focus on testing actual behavior, not mock setups.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from core.chat_tab_tools import ToolHandler


class TestToolHandlerInitialization:
    """Tests for ToolHandler initialization."""

    def test_init_creates_handler_with_defaults(self) -> None:
        """Test that initialization creates handler with correct defaults."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        assert handler._chat is mock_chat
        assert handler._continue is callback
        assert handler._pending_count == 0
        assert hasattr(handler._pending_lock, "acquire") and callable(
            handler._pending_lock.acquire,
        )

    def test_init_lock_is_threading_lock(self) -> None:
        """Test that initialization creates a threading.Lock."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        assert hasattr(handler._pending_lock, "acquire") and callable(
            handler._pending_lock.acquire,
        )


class TestToolHandlerHandleToolCall:
    """Tests for handle_tool_call method."""

    def test_handle_tool_call_nested_format(self) -> None:
        """Test handling tool call with nested format."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat._app_core.call_mcp_tool = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        tool_json = json.dumps(
            {
                "tool_call": {
                    "name": "server_tool",
                    "arguments": {"arg1": "value1"},
                    "id": "tool-123",
                },
            },
        )

        with patch.object(threading, "Thread") as mock_thread:
            mock_thread_instance = Mock()
            mock_thread.return_value = mock_thread_instance

            result = handler.handle_tool_call(tool_json, 0)

            # Should return the tool ID
            assert result == "tool-123"
            # Should increment pending count
            assert handler._pending_count == 1
            # Should start a thread
            mock_thread.assert_called_once()
            mock_thread_instance.start.assert_called_once()

    def test_handle_tool_call_flat_format(self) -> None:
        """Test handling tool call with flat format."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat._app_core.call_mcp_tool = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        tool_json = json.dumps(
            {
                "name": "server_tool",
                "arguments": {"arg1": "value1"},
                "id": "tool-456",
            },
        )

        with patch.object(threading, "Thread") as mock_thread:
            mock_thread_instance = Mock()
            mock_thread.return_value = mock_thread_instance

            result = handler.handle_tool_call(tool_json, 0)

            # Should return the tool ID
            assert result == "tool-456"

    def test_handle_tool_call_no_id_generates_uuid(self) -> None:
        """Test that tool call without ID generates a UUID."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat._app_core.call_mcp_tool = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        tool_json = json.dumps(
            {
                "tool_call": {
                    "name": "server_tool",
                    "arguments": {"arg1": "value1"},
                },
            },
        )

        with patch.object(threading, "Thread") as mock_thread:
            mock_thread_instance = Mock()
            mock_thread.return_value = mock_thread_instance

            result = handler.handle_tool_call(tool_json, 0)

            # Should generate a UUID-based ID
            assert result is not None
            assert "server_tool" in result

    def test_handle_tool_call_invalid_json_returns_none(self) -> None:
        """Test that invalid JSON returns None."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        result = handler.handle_tool_call("invalid json", 0)

        assert result is None

    def test_handle_tool_call_persists_to_chat_state(self) -> None:
        """Test that tool call is persisted to chat state."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat._app_core.call_mcp_tool = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        tool_json = json.dumps(
            {
                "tool_call": {
                    "name": "server_tool",
                    "arguments": {"arg1": "value1"},
                    "id": "tool-123",
                },
            },
        )

        with patch.object(threading, "Thread"):
            handler.handle_tool_call(tool_json, 0)

            # Should persist to chat state
            mock_chat.chat_state.add_tool_call_to_answer.assert_called_once()
            call_args = mock_chat.chat_state.add_tool_call_to_answer.call_args
            assert call_args[0][0] == 0  # answer_index
            assert call_args[0][1] == tool_json  # tool_json
            assert call_args[0][2] == "tool-123"  # tc_store_id


class TestToolHandlerStreamGuard:
    """Tests for mark_stream_active/mark_stream_finished.

    Regression coverage for a race where a fast tool call (e.g. get_time)
    could finish and decrement pending_count to 0 *before* the original
    stream had read a later chunk containing another tool call — firing a
    continuation request while the original stream was still being
    processed. Two process_stream() calls then appended to the same answer
    concurrently, interleaving their content word-by-word. Counting the
    stream's own lifetime as a pending unit (alongside individual tool
    calls) closes the race: continuation can't fire until the stream that
    detected the tool calls has also finished.
    """

    def test_fast_tool_finishing_does_not_fire_continuation_while_stream_active(
        self,
    ) -> None:
        mock_chat = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler.mark_stream_active()  # stream starts reading
        handler._pending_count += 1  # a tool call is detected mid-stream
        handler._pending_count -= 1  # ...and finishes almost instantly

        # Simulate the "last man standing" check a finishing tool runs,
        # without going through the real threaded _execute_tool.
        with handler._pending_lock:
            remaining = handler._pending_count
        if remaining == 0:
            handler._continue(0)

        # Must NOT fire — the stream itself hasn't finished yet.
        callback.assert_not_called()

    def test_stream_with_no_tool_calls_never_fires_continuation(self) -> None:
        """Regression test for the exact bug this guard introduced: a plain

        (tool-free) turn must NOT trigger a continuation when its stream
        finishes. mark_stream_active/finished bracket every stream, tool or
        not — without the _has_tool_calls gate, a complete answer would
        "continue" itself, generating another complete answer, whose stream
        finishing would continue *that* one, looping forever until the user
        hits stop.
        """
        mock_chat = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler.mark_stream_active()
        handler.mark_stream_finished(answer_index=0)

        callback.assert_not_called()

    def test_continuation_fires_once_stream_and_all_tools_are_done(self) -> None:
        mock_chat = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler.mark_stream_active()
        with handler._pending_lock:
            handler._pending_count += 1  # a tool call was detected
            handler._has_tool_calls = True
        with handler._pending_lock:
            handler._pending_count -= 1  # ...and finished
        handler.mark_stream_finished(answer_index=0)

        callback.assert_called_once_with(0)

    def test_continuation_waits_for_slow_tool_after_stream_finishes(self) -> None:
        mock_chat = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler.mark_stream_active()
        with handler._pending_lock:
            handler._pending_count += 1  # tool call detected, still running
            handler._has_tool_calls = True

        handler.mark_stream_finished(answer_index=0)  # stream itself is done
        callback.assert_not_called()  # tool is still pending

        handler._release_pending_unit(0)  # tool finishes last

        callback.assert_called_once_with(0)

    def test_mark_stream_finished_respects_stop_flag(self) -> None:
        mock_chat = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.stop_streaming_flag.set()
        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler.mark_stream_active()
        with handler._pending_lock:
            handler._pending_count += 1
            handler._has_tool_calls = True
        handler._release_pending_unit(0)
        handler.mark_stream_finished(answer_index=0)

        callback.assert_not_called()

    def test_real_fast_tool_via_execute_tool_does_not_fire_while_stream_active(
        self,
    ) -> None:
        """End-to-end through the real _execute_tool path (not manual counter

        manipulation): a tool that resolves synchronously/instantly must not
        trigger continuation while the stream that detected it is still
        marked active — reproducing the original race with the actual code
        path instead of simulating it.
        """
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = Mock()
        mock_chat._app_core.api = Mock()
        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        # Stream is mid-read and has just detected this tool call.
        handler.mark_stream_active()
        with handler._pending_lock:
            handler._pending_count += 1
            handler._has_tool_calls = True

        def instant_tool(server, tool, args, cb):
            cb({"result": "fast"})  # resolves before the stream reads on

        mock_chat._app_core.call_mcp_tool = instant_tool
        handler._execute_tool("server", "get_time", {}, 0, "tool-fast")

        # The tool finishing must NOT fire continuation — the stream itself
        # (counted via mark_stream_active) hasn't finished yet.
        callback.assert_not_called()

        # Only once the stream itself finishes does continuation fire.
        handler.mark_stream_finished(0)
        callback.assert_called_once_with(0)


class TestToolExecutionTimeout:
    """Regression coverage for the per-tool-call wait timeout.

    Must comfortably exceed every timeout a tool legitimately supports on
    its own — run_shell_command alone allows up to shell_executor.MAX_TIMEOUT
    (300s) — otherwise _execute_tool reports "timed out" on tools that were
    still genuinely working within their own requested budget (e.g. a long
    shell command, or fetch_url_as_markdown's curl fetch plus a separate,
    uncapped LLM summarization round-trip).
    """

    def test_timeout_exceeds_shell_executors_max_timeout(self) -> None:
        from shell_executor import MAX_TIMEOUT

        assert ToolHandler.TOOL_EXECUTION_TIMEOUT_SECONDS > MAX_TIMEOUT

    def test_execute_tool_waits_using_the_class_constant(self) -> None:
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = Mock()
        mock_chat._app_core.api = Mock()

        handler = ToolHandler(mock_chat, Mock())
        handler._pending_count = 1
        handler._has_tool_calls = True

        def mock_call_mcp_tool(server, tool, args, cb):
            cb({"result": "ok"})

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        with patch.object(
            threading.Event,
            "wait",
            return_value=True,
        ) as mock_wait:
            handler._execute_tool("server", "tool", {}, 0, "tool-123")

        mock_wait.assert_called_once_with(
            timeout=ToolHandler.TOOL_EXECUTION_TIMEOUT_SECONDS,
        )


class TestToolHandlerExecuteTool:
    """Tests for _execute_tool method."""

    def test_execute_tool_success_triggers_callback(self) -> None:
        """Test successful tool execution triggers continuation callback."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = Mock()

        # Mock the API
        mock_api = Mock()
        mock_api.wait_for_fold_rendered.return_value = True
        mock_chat._app_core.api = mock_api

        callback = Mock()

        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1  # Simulate one pending
        handler._has_tool_calls = True

        # Simulate successful tool call
        def mock_call_mcp_tool(server, tool, args, cb):
            cb({"result": "success"})

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        # Execute directly (normally runs in thread)
        handler._execute_tool("server", "tool", {}, 0, "tool-123")

        # Should decrement pending count
        assert handler._pending_count == 0
        # Should trigger continuation callback (last man standing)
        callback.assert_called_once_with(0)

    def test_execute_tool_timeout_no_callback(self) -> None:
        """Test that timeout doesn't trigger callback."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = Mock()
        mock_chat._app_core.api = Mock()

        callback = Mock()

        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1
        handler._has_tool_calls = True

        # Simulate timeout (callback never called)
        def mock_call_mcp_tool(server, tool, args, cb):
            pass  # Never call the callback

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        # Execute with short timeout by patching Event.wait
        with patch.object(threading.Event, "wait", return_value=False):
            handler._execute_tool("server", "tool", {}, 0, "tool-123")

        # Should still decrement pending count
        assert handler._pending_count == 0
        # Should trigger callback since pending is 0
        callback.assert_called_once_with(0)

    def test_execute_tool_multiple_pending_no_callback(self) -> None:
        """Test that multiple pending tools don't trigger callback."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = Mock()
        mock_chat._app_core.api = Mock()

        callback = Mock()

        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 2  # Two pending
        handler._has_tool_calls = True

        def mock_call_mcp_tool(server, tool, args, cb):
            cb({"result": "success"})

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        handler._execute_tool("server", "tool", {}, 0, "tool-123")

        # Should decrement but not to 0
        assert handler._pending_count == 1
        # Should NOT trigger callback (not last man standing)
        callback.assert_not_called()

    def test_execute_tool_stop_flag_set_no_callback(self) -> None:
        """Test that stop flag prevents continuation callback."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.stop_streaming_flag.set()  # Set the stop flag
        mock_chat.content_update_queue = Mock()
        mock_chat._app_core.api = Mock()

        callback = Mock()

        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1
        handler._has_tool_calls = True

        def mock_call_mcp_tool(server, tool, args, cb):
            cb({"result": "success"})

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        handler._execute_tool("server", "tool", {}, 0, "tool-123")

        # Should NOT trigger callback when stop flag is set
        callback.assert_not_called()

    def test_execute_tool_persists_result(self) -> None:
        """Test that tool result is persisted to chat state."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = Mock()
        mock_chat._app_core.api = Mock()

        callback = Mock()

        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1

        result_data = {"result": "tool output"}

        def mock_call_mcp_tool(server, tool, args, cb):
            cb(result_data)

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        handler._execute_tool("server", "tool", {}, 0, "tool-123")

        # Should persist result to chat state
        mock_chat.chat_state.add_tool_result_to_answer.assert_called_once()
        call_args = mock_chat.chat_state.add_tool_result_to_answer.call_args
        assert call_args[0][0] == 0  # answer_index
        assert "tool-123" in call_args[0][2]  # tool_id

    def test_execute_tool_sends_to_ui(self) -> None:
        """Test that tool result is sent to UI."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = MagicMock()

        mock_api = Mock()
        mock_api.wait_for_fold_rendered.return_value = True
        mock_chat._app_core.api = mock_api

        callback = Mock()

        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1

        def mock_call_mcp_tool(server, tool, args, cb):
            cb({"result": "tool output"})

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        handler._execute_tool("server", "tool", {}, 0, "tool-123")

        # Should send to UI via API
        mock_api.on_content_update.assert_called_once()


class TestToolHandlerExecuteToolGating:
    """Tests for output-gating of oversized results in _execute_tool."""

    def test_large_result_is_gated_before_persistence(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Oversized results should be truncated with a pointer, not stored whole."""
        import core.tool_output_gate as gate_module

        monkeypatch.setattr(gate_module, "GATE_THRESHOLD_BYTES", 100)
        monkeypatch.setattr(gate_module, "PREVIEW_MAX_BYTES", 20)
        monkeypatch.setattr(gate_module, "PREVIEW_MAX_LINES", 1)
        monkeypatch.setattr(
            gate_module,
            "TOOL_OUTPUT_TEMP_ROOT",
            tmp_path / "alpaca_assist_tool_outputs",
        )

        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = MagicMock()
        mock_chat.tab_id = "tab-gating-test"
        mock_chat._app_core.api = Mock()

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1

        large_text = "x" * 1000

        def mock_call_mcp_tool(server, tool, args, cb):
            cb({"result": large_text})

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        handler._execute_tool("server", "tool", {}, 0, "tool-123")

        call_args = mock_chat.chat_state.add_tool_result_to_answer.call_args
        persisted = call_args[0][1]
        assert "Output truncated" in persisted
        assert large_text not in persisted

    def test_small_result_passes_through_unchanged(self) -> None:
        """Results under the threshold should be unaffected by gating."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = MagicMock()
        mock_chat.tab_id = "tab-gating-test"
        mock_chat._app_core.api = Mock()

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1

        def mock_call_mcp_tool(server, tool, args, cb):
            cb({"result": "small output"})

        mock_chat._app_core.call_mcp_tool = mock_call_mcp_tool

        handler._execute_tool("server", "tool", {}, 0, "tool-123")

        call_args = mock_chat.chat_state.add_tool_result_to_answer.call_args
        persisted = call_args[0][1]
        assert "Output truncated" not in persisted
        assert "small output" in persisted


class TestToolHandlerFormatResult:
    """Tests for _format_result method."""

    def test_format_result_simple_dict_with_result_key(self) -> None:
        """Test formatting simple dict with result key."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        result = handler._format_result({"result": "output text"})
        assert result == "output text"

    def test_format_result_simple_dict_with_content_key(self) -> None:
        """Test formatting simple dict with content key."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        result = handler._format_result({"content": "content text"})
        assert result == "content text"

    def test_format_result_mcp_style_content_array(self) -> None:
        """Test formatting MCP-style content array."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        result = handler._format_result(
            {
                "content": [
                    {"type": "text", "text": "Line 1"},
                    {"type": "text", "text": "Line 2"},
                ],
            },
        )
        assert result == "Line 1\nLine 2"

    def test_format_result_complex_dict_json(self) -> None:
        """Test formatting complex dict as JSON."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        complex_dict = {"key1": "value1", "key2": "value2"}
        result = handler._format_result(complex_dict)
        assert "key1" in result
        assert "key2" in result
        assert "value1" in result

    def test_format_result_non_dict(self) -> None:
        """Test formatting non-dict as string."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        result = handler._format_result("plain string")
        assert result == "plain string"


class TestToolHandlerInjectResultFold:
    """Tests for _inject_result_fold method."""

    def test_inject_result_fold_with_api(self) -> None:
        """Test injecting result fold when API is available."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.tab_id = "test-tab"

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler._inject_result_fold(0, "result text", "fold-123")

        mock_api.inject_tool_fold.assert_called_once_with(
            "test-tab",
            "fold-123",
            "result",
            "result text",
            0,
        )

    def test_inject_result_fold_no_api_does_not_raise(self) -> None:
        """Test that inject doesn't raise when API is None."""
        mock_chat = Mock()
        mock_chat._app_core.api = None

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        # Should not raise
        handler._inject_result_fold(0, "result text", "fold-123")

    def test_inject_result_fold_auto_generates_id(self) -> None:
        """Test that fold ID is auto-generated if not provided."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.tab_id = "test-tab"

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler._inject_result_fold(0, "result text", None)

        # Should have called with auto-generated ID
        call_args = mock_api.inject_tool_fold.call_args
        assert call_args[0][1].startswith("fold-result-0-")


class TestToolHandlerInjectCallFold:
    """Tests for inject_call_fold method."""

    def test_inject_call_fold_with_api(self) -> None:
        """Test injecting call fold when API is available."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.tab_id = "test-tab"

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        handler.inject_call_fold(0, '{"tool_call": {}}', "tool-123")

        mock_api.inject_tool_fold.assert_called_once_with(
            "test-tab",
            "fold-call-0-tool-123",
            "call",
            '{"tool_call": {}}',
            0,
        )

    def test_inject_call_fold_no_api_does_not_raise(self) -> None:
        """Test that inject doesn't raise when API is None."""
        mock_chat = Mock()
        mock_chat._app_core.api = None

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        # Should not raise
        handler.inject_call_fold(0, '{"tool_call": {}}', "tool-123")


class TestToolHandlerPrepareContinuationMessages:
    """Tests for prepare_continuation_messages method."""

    def test_prepare_with_no_tools(self) -> None:
        """Test preparing messages with no tool calls."""
        mock_chat = Mock()
        mock_chat.chat_state.questions = ["Question 1?", "Question 2?"]

        # Mock answers without tool components
        mock_answer1 = Mock()
        mock_answer1.components = ["Answer 1 text."]
        mock_answer2 = Mock()
        mock_answer2.components = ["Answer 2 text."]
        mock_chat.chat_state.answers = [mock_answer1, mock_answer2]

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        messages = handler.prepare_continuation_messages(1)

        # Should have user/assistant pairs
        assert len(messages) >= 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_prepare_with_tool_call_and_result(self) -> None:
        """Test preparing messages with tool call and result."""
        from chat_state import ToolCall, ToolResult

        mock_chat = Mock()
        mock_chat.chat_state.questions = ["Question?"]

        # Mock answer with tool components
        mock_answer = Mock()
        mock_answer.components = [
            "Pre-tool text.",
            ToolCall('{"tool_call": {"name": "tool"}}', "tool-123"),
            ToolResult("Tool result content", "tool-123"),
            "Post-tool text.",
        ]
        mock_chat.chat_state.answers = [mock_answer]

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        messages = handler.prepare_continuation_messages(0)

        # Should have tool_use_call and tool_result messages
        tool_messages = [
            m for m in messages if m["role"] in ("tool_use_call", "tool_result")
        ]
        assert len(tool_messages) == 2
        assert tool_messages[0]["role"] == "tool_use_call"
        assert tool_messages[1]["role"] == "tool_result"

    def test_prepare_matches_tc_tr_by_id(self) -> None:
        """Test that TC and TR are matched by ID."""
        from chat_state import ToolCall, ToolResult

        mock_chat = Mock()
        mock_chat.chat_state.questions = ["Question?"]

        # Mock answer with mismatched IDs
        mock_answer = Mock()
        mock_answer.components = [
            ToolCall('{"tool_call": {"name": "tool1"}}', "tool-1"),
            ToolCall('{"tool_call": {"name": "tool2"}}', "tool-2"),
            ToolResult("Result for tool2", "tool-2"),
            ToolResult("Result for tool1", "tool-1"),
        ]
        mock_chat.chat_state.answers = [mock_answer]

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        messages = handler.prepare_continuation_messages(0)

        # Should have 2 tool_use_call and 2 tool_result messages
        tool_calls = [m for m in messages if m["role"] == "tool_use_call"]
        tool_results = [m for m in messages if m["role"] == "tool_result"]
        assert len(tool_calls) == 2
        assert len(tool_results) == 2


class TestToolHandlerParseForMessage:
    """Tests for _parse_for_message method."""

    def test_parse_nested_format(self) -> None:
        """Test parsing nested format."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        tool_json = '{"tool_call": {"name": "test_tool", "arguments": {"arg": "val"}}}'
        result = handler._parse_for_message(tool_json, "tool-123")

        assert result is not None
        assert result["id"] == "tool-123"
        assert result["name"] == "test_tool"
        assert result["arguments"] == {"arg": "val"}

    def test_parse_flat_format(self) -> None:
        """Test parsing flat format."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        tool_json = '{"name": "test_tool", "arguments": {"arg": "val"}}'
        result = handler._parse_for_message(tool_json, "tool-123")

        assert result is not None
        assert result["id"] == "tool-123"
        assert result["name"] == "test_tool"

    def test_parse_invalid_json_returns_fallback(self) -> None:
        """Test that invalid JSON returns fallback data."""
        mock_chat = Mock()
        callback = Mock()

        handler = ToolHandler(mock_chat, callback)

        result = handler._parse_for_message("invalid json", "tool-123")

        assert result is not None
        assert result["id"] == "tool-123"
        assert result["name"] == "unknown_tool"
        assert result["arguments"] == {}


class TestToolHandlerPutContentUpdate:
    """Tests for _put_content_update method."""

    def test_put_update_success(self) -> None:
        """Test successful queue put."""
        mock_chat = Mock()
        mock_chat.content_update_queue = MagicMock()
        mock_chat.content_update_queue.put.return_value = None

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        from utils import ContentUpdate

        update = ContentUpdate(answer_index=0, content_chunk="test", is_done=False)
        handler._put_content_update(update)

        mock_chat.content_update_queue.put.assert_called_once()

    def test_put_update_retries_on_failure(self) -> None:
        """Test that put retries on failure."""
        import queue

        mock_chat = Mock()
        mock_chat.content_update_queue = MagicMock()
        # First two calls fail, third succeeds
        mock_chat.content_update_queue.put.side_effect = [
            queue.Full,
            queue.Full,
            None,
        ]

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        from utils import ContentUpdate

        update = ContentUpdate(answer_index=0, content_chunk="test", is_done=False)

        with patch("time.sleep"):  # Don't actually sleep
            handler._put_content_update(update)

        # Should have been called 3 times
        assert mock_chat.content_update_queue.put.call_count == 3

    def test_put_update_gives_up_after_max_retries(self) -> None:
        """Test that put gives up after max retries."""
        import queue

        mock_chat = Mock()
        mock_chat.content_update_queue = MagicMock()
        # All calls fail
        mock_chat.content_update_queue.put.side_effect = queue.Full

        callback = Mock()
        handler = ToolHandler(mock_chat, callback)

        from utils import ContentUpdate

        update = ContentUpdate(answer_index=0, content_chunk="test", is_done=False)

        with patch("time.sleep"):  # Don't actually sleep
            handler._put_content_update(update)

        # Should have been called 3 times (max_retries)
        assert mock_chat.content_update_queue.put.call_count == 3
