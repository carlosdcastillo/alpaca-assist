"""Tests for chat_tab_summary.py module.

This module tests the SummaryHandler class with minimal mocking where possible.
Focus on testing actual behavior, not mock setups.
"""

from __future__ import annotations

import queue
import threading
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
import requests

from chat_state import FullAnswer
from core.chat_tab_summary import SummaryHandler


class TestSummaryHandlerInitialization:
    """Tests for SummaryHandler initialization."""

    def test_init_creates_handler_with_defaults(self) -> None:
        """Test that initialization creates handler with correct defaults."""
        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat.preferences = {"model": "llama3.1"}

        handler = SummaryHandler(mock_chat)

        assert handler._chat is mock_chat
        assert handler._generated is False
        assert hasattr(handler._lock, "acquire") and callable(handler._lock.acquire)

    def test_init_lock_is_threading_lock(self) -> None:
        """Test that initialization creates a threading.Lock."""
        mock_chat = Mock()
        handler = SummaryHandler(mock_chat)

        assert hasattr(handler._lock, "acquire") and callable(handler._lock.acquire)


class TestSummaryHandlerTriggerIfNeeded:
    """Tests for trigger_if_needed method."""

    def test_trigger_if_needed_already_generated_skips(self) -> None:
        """Test that trigger_if_needed skips if already generated."""
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy.return_value = (
            ["Question?"],
            [Mock(get_text_only_content=Mock(return_value="Answer."))],
            None,
        )
        mock_chat.preferences = {"model": "llama3.1"}

        handler = SummaryHandler(mock_chat)
        handler._generated = True

        with patch.object(threading, "Timer") as mock_timer:
            handler.trigger_if_needed()
            mock_timer.assert_not_called()

    def test_trigger_if_needed_no_content_updates_title(self) -> None:
        """Test that trigger_if_needed updates title if no content available."""
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy.return_value = ([], [], None)
        mock_chat._app_core.api = Mock()

        handler = SummaryHandler(mock_chat)

        handler.trigger_if_needed()

        # Title should be updated to fallback
        assert handler._generated is True
        mock_chat._app_core.api.update_tab_title.assert_called_once()

    def test_trigger_if_needed_starts_generation_with_timer(self) -> None:
        """Test that trigger_if_needed starts generation with 2.0s timer."""
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy.return_value = (
            ["Question?"],
            [Mock(get_text_only_content=Mock(return_value="Answer."))],
            None,
        )
        mock_chat.preferences = {"model": "llama3.1"}

        handler = SummaryHandler(mock_chat)

        with patch.object(threading, "Timer") as mock_timer:
            mock_timer_instance = Mock()
            mock_timer.return_value = mock_timer_instance

            handler.trigger_if_needed()

            # Should start timer with 2.0s delay
            mock_timer.assert_called_once()
            assert mock_timer.call_args[0][0] == 2.0
            mock_timer_instance.start.assert_called_once()


class TestSummaryHandlerFetch:
    """Tests for _fetch method."""

    def test_fetch_successful_response(self) -> None:
        """Test that _fetch puts summary in queue on success."""
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy_full.return_value = (
            ["What is Python?"],
            [FullAnswer(["Python is a programming language."])],
            None,
        )
        mock_chat.preferences = {
            "model": "llama3.1",
            "api_url": "http://localhost:11434",
        }
        mock_chat._app_core.api = Mock()

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()

        # Mock the response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_response.iter_lines.return_value = [
            '{"message": {"content": "Programming"}}',
            '{"message": {"content": " Language"}}',
            '{"message": {"content": " Overview"}, "done": true}',
        ]

        with patch("requests.post", return_value=mock_response):
            handler._fetch(summary_queue)

        result = summary_queue.get(timeout=1)
        assert result == "Programming Language Overview"

    def test_fetch_falls_back_to_default_model_when_unset(self) -> None:
        """If preferences has no "model" key, request the app's real default —

        not the "llama3.1" placeholder from an old local-Ollama setup, which
        isn't one of this app's actual models.
        """
        from anthropic_ollama_server import DEFAULT_MODEL

        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy_full.return_value = (
            ["What is Python?"],
            [FullAnswer(["Python is a programming language."])],
            None,
        )
        mock_chat.preferences = {"api_url": "http://localhost:11434"}
        mock_chat._app_core.api = Mock()

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_response.iter_lines.return_value = [
            '{"message": {"content": "Summary"}, "done": true}',
        ]

        with patch("requests.post", return_value=mock_response) as mock_post:
            handler._fetch(summary_queue)

        assert mock_post.call_args.kwargs["json"]["model"] == DEFAULT_MODEL

    def test_fetch_api_error_puts_fallback(self) -> None:
        """Test that _fetch puts fallback on API error."""
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy.return_value = (
            ["Question?"],
            ["Answer."],
            None,
        )
        mock_chat.preferences = {
            "model": "llama3.1",
            "api_url": "http://localhost:11434",
        }

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()

        # Mock error response
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch("requests.post", return_value=mock_response):
            handler._fetch(summary_queue)

        result = summary_queue.get(timeout=1)
        assert result == "Chat Summary"

    def test_fetch_timeout_puts_fallback(self) -> None:
        """Test that _fetch puts fallback on timeout."""
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy.return_value = (
            ["Question?"],
            ["Answer."],
            None,
        )
        mock_chat.preferences = {
            "model": "llama3.1",
            "api_url": "http://localhost:11434",
        }

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()

        with patch("requests.post", side_effect=requests.exceptions.Timeout):
            handler._fetch(summary_queue)

        result = summary_queue.get(timeout=1)
        assert result == "Chat Summary"

    def test_fetch_connection_error_puts_fallback(self) -> None:
        """Test that _fetch puts fallback on connection error."""
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy.return_value = (
            ["Question?"],
            ["Answer."],
            None,
        )
        mock_chat.preferences = {
            "model": "llama3.1",
            "api_url": "http://localhost:11434",
        }

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()

        with patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        ):
            handler._fetch(summary_queue)

        result = summary_queue.get(timeout=1)
        assert result == "Chat Summary"

    def test_fetch_retries_on_empty_content(self) -> None:
        """Test that _fetch retries when content is not ready."""
        mock_chat = Mock()
        # First call returns empty, second call returns content
        call_count = [0]

        def get_safe_copy_full() -> tuple[list[str], list[FullAnswer], None]:
            call_count[0] += 1
            if call_count[0] == 1:
                return ([], [], None)  # Empty first call
            return (
                ["Question?"],
                [FullAnswer(["Answer."])],
                None,
            )

        mock_chat.chat_state.get_safe_copy_full = get_safe_copy_full
        mock_chat.preferences = {
            "model": "llama3.1",
            "api_url": "http://localhost:11434",
        }
        mock_chat._app_core.api = Mock()

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()

        # Mock the response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_response.iter_lines.return_value = [
            '{"message": {"content": "Summary"}, "done": true}',
        ]

        with patch("requests.post", return_value=mock_response):
            handler._fetch(summary_queue)

        result = summary_queue.get(timeout=1)
        assert result == "Summary"
        assert call_count[0] >= 2  # Should have retried


class TestSummaryHandlerClean:
    """Tests for _clean method."""

    def test_clean_trims_and_capitalizes(self) -> None:
        """Test that _clean trims, removes quotes, and capitalizes."""
        mock_chat = Mock()
        handler = SummaryHandler(mock_chat)

        result = handler._clean('  "python programming"  ')
        assert result == "Python programming"

    def test_clean_removes_newlines(self) -> None:
        """Test that _clean replaces newlines with spaces."""
        mock_chat = Mock()
        handler = SummaryHandler(mock_chat)

        result = handler._clean("Line1\nLine2\nLine3")
        assert result == "Line1 Line2 Line3"

    def test_clean_truncates_to_50_chars(self) -> None:
        """Test that _clean truncates to 50 characters."""
        mock_chat = Mock()
        handler = SummaryHandler(mock_chat)

        long_text = "A" * 100
        result = handler._clean(long_text)
        assert len(result) == 50
        assert result == "A" * 50

    def test_clean_empty_returns_default(self) -> None:
        """Test that _clean returns default for empty input."""
        mock_chat = Mock()
        handler = SummaryHandler(mock_chat)

        result = handler._clean("   ")
        assert result == "Chat Summary"


class TestSummaryHandlerUpdateTitle:
    """Tests for _update_title method."""

    def test_update_title_updates_chat_and_api(self) -> None:
        """Test that _update_title updates both chat title and API."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.tab_id = "test-tab"

        handler = SummaryHandler(mock_chat)
        handler._update_title("New Title")

        assert mock_chat.title == "New Title"
        mock_api.update_tab_title.assert_called_once_with("test-tab", "New Title")

    def test_update_title_no_api_does_not_raise(self) -> None:
        """Test that _update_title doesn't raise if API is None."""
        mock_chat = Mock()
        mock_chat._app_core.api = None

        handler = SummaryHandler(mock_chat)
        # Should not raise
        handler._update_title("Title")


class TestSummaryHandlerRecomputeTitle:
    """Tests for explicitly regenerating a conversation title."""

    def test_recompute_starts_generation_immediately(self) -> None:
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy_full.return_value = (
            ["What is Python?"],
            [FullAnswer(["A programming language."])],
            None,
        )
        handler = SummaryHandler(mock_chat)

        with patch.object(handler, "_start_generation") as start:
            result = handler.recompute_title()

        assert result == {"started": True}
        assert handler._generated is True
        start.assert_called_once_with(1)

    def test_recompute_rejects_empty_conversation(self) -> None:
        mock_chat = Mock()
        mock_chat.chat_state.get_safe_copy_full.return_value = ([], [], None)
        handler = SummaryHandler(mock_chat)

        result = handler.recompute_title()

        assert result == {"started": False, "reason": "empty"}

    def test_stale_generation_does_not_replace_newer_title(self) -> None:
        mock_chat = Mock()
        mock_chat._app_core.api = Mock()
        mock_chat.tab_id = "test-tab"
        handler = SummaryHandler(mock_chat)
        handler._generation = 2
        summary_queue: queue.Queue[str] = queue.Queue()
        summary_queue.put("Stale Title")

        handler._handle_response(summary_queue, generation=1)

        mock_chat._app_core.api.update_tab_title.assert_not_called()


class TestSummaryHandlerGetTitleFromQuestion:
    """Tests for get_title_from_first_question method."""

    def test_get_title_from_first_question_returns_truncated(self) -> None:
        """Test that get_title_from_first_question returns truncated question."""
        mock_chat = Mock()
        mock_chat.chat_state.questions = ["What is Python programming language?"]

        handler = SummaryHandler(mock_chat)
        result = handler.get_title_from_first_question()

        assert result == "What is Python programming language?"

    def test_get_title_from_first_question_truncates_long(self) -> None:
        """Test that get_title_from_first_question truncates long questions."""
        mock_chat = Mock()
        mock_chat.chat_state.questions = ["A" * 100]

        handler = SummaryHandler(mock_chat)
        result = handler.get_title_from_first_question()

        assert result is not None
        assert len(result) == 53  # 50 chars + "..."
        assert result.endswith("...")

    def test_get_title_from_first_question_no_questions_returns_none(self) -> None:
        """Test that get_title_from_first_question returns None if no questions."""
        mock_chat = Mock()
        mock_chat.chat_state.questions = []

        handler = SummaryHandler(mock_chat)
        result = handler.get_title_from_first_question()

        assert result is None


class TestNormalizeApiUrl:
    """Tests for utils.normalize_api_url (formerly SummaryHandler._normalize_api_url)."""

    def test_normalize_removes_trailing_slash(self) -> None:
        """Test that normalize removes trailing slash."""
        import utils

        result = utils.normalize_api_url("http://localhost:11434/")
        assert result == "http://localhost:11434"

    def test_normalize_removes_api_chat_suffix(self) -> None:
        """Test that normalize removes /api/chat suffix."""
        import utils

        result = utils.normalize_api_url("http://localhost:11434/api/chat")
        assert result == "http://localhost:11434"

    def test_normalize_handles_both(self) -> None:
        """Test that normalize handles both trailing slash and /api/chat."""
        import utils

        result = utils.normalize_api_url("http://localhost:11434/api/chat/")
        assert result == "http://localhost:11434"

    def test_normalize_no_change_needed(self) -> None:
        """Test that normalize returns unchanged if already normalized."""
        import utils

        result = utils.normalize_api_url("http://localhost:11434")
        assert result == "http://localhost:11434"


class TestSummaryHandlerHandleResponse:
    """Tests for _handle_response method."""

    def test_handle_response_updates_title(self) -> None:
        """Test that _handle_response updates title from queue."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.tab_id = "test-tab"

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()
        summary_queue.put("Generated Summary")

        handler._handle_response(summary_queue)

        mock_api.update_tab_title.assert_called_once_with(
            "test-tab",
            "Generated Summary",
        )

    def test_handle_response_empty_queue_uses_fallback(self) -> None:
        """Test that _handle_response uses fallback on empty queue."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.tab_id = "test-tab"

        handler = SummaryHandler(mock_chat)
        summary_queue: queue.Queue[str] = queue.Queue()

        handler._handle_response(summary_queue)

        mock_api.update_tab_title.assert_called_once_with("test-tab", "Chat Summary")


class TestSummaryHandlerUpdateTitleFromQuestion:
    """Tests for update_title_from_question method."""

    def test_update_title_from_question_updates_api(self) -> None:
        """Test that update_title_from_question updates via API."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.tab_id = "test-tab"
        mock_chat.chat_state.questions = ["What is Python?"]

        handler = SummaryHandler(mock_chat)
        handler.update_title_from_question()

        mock_api.update_tab_title.assert_called_once_with("test-tab", "What is Python?")

    def test_update_title_from_question_no_api_does_not_raise(self) -> None:
        """Test that update_title_from_question doesn't raise if API is None."""
        mock_chat = Mock()
        mock_chat._app_core.api = None
        mock_chat.chat_state.questions = ["What is Python?"]

        handler = SummaryHandler(mock_chat)
        # Should not raise
        handler.update_title_from_question()

    def test_update_title_from_question_no_questions_does_nothing(self) -> None:
        """Test that update_title_from_question does nothing if no questions."""
        mock_chat = Mock()
        mock_api = Mock()
        mock_chat._app_core.api = mock_api
        mock_chat.chat_state.questions = []

        handler = SummaryHandler(mock_chat)
        handler.update_title_from_question()

        mock_api.update_tab_title.assert_not_called()
