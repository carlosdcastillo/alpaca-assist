"""Refactored ChatTab - thin orchestration layer.

This module provides the public API facade that coordinates all subsystems.
All actual work is delegated to handler classes:
    - StreamingHandler: Manages streaming lifecycle
    - ToolHandler: Handles tool calls and execution
    - SummaryHandler: Generates conversation summaries
    - StreamProcessor: Processes SSE streams (via StreamingHandler)

Key Classes:
    ChatTab: Public API facade with backward-compatible interface.

Architecture:
    Thin facade pattern that maintains backward compatibility while
    delegating all work to focused handler modules.
"""
from __future__ import annotations

import logging
from typing import Any
from typing import TYPE_CHECKING

from core.chat_tab_base import ChatTabBase
from core.chat_tab_streaming import StreamingHandler
from core.chat_tab_summary import SummaryHandler
from core.chat_tab_tools import ToolHandler

if TYPE_CHECKING:
    from core.app_core import AppCore
    from webview_api import WebViewAPI

logger = logging.getLogger(__name__)


class ChatTab(ChatTabBase):
    """Public API for chat tab functionality.

    This is a thin orchestration layer that delegates all work to
    specialized handler classes. Maintains backward-compatible public
    interface while providing a clean, testable architecture.
    """

    def __init__(
        self,
        tab_id: str,
        title: str,
        app_core: AppCore,
        conversation_id: int,
    ) -> None:
        """Initialize ChatTab with handler subsystems.

        Args:
            tab_id: Unique identifier for this tab.
            title: Initial tab title.
            app_core: Reference to the application core.
            conversation_id: Permanent ID allocated before tab creation.
        """
        super().__init__(tab_id, title, app_core, conversation_id)

        # Order matters: ToolHandler must exist before StreamingHandler
        # because StreamingHandler needs ToolHandler for continuation
        self._tool_handler = ToolHandler(
            self,
            continuation_callback=self._on_tool_continuation,
        )
        self._streaming_handler = StreamingHandler(
            self,
            tool_handler=self._tool_handler,
        )
        self._summary_handler = SummaryHandler(self)

    def _get_api(self) -> WebViewAPI:
        """Get the WebViewAPI, raising if not available.

        Returns:
            The WebViewAPI instance.

        Raises:
            RuntimeError: If the API is not initialized.
        """
        api = self._app_core.api
        if api is None:
            raise RuntimeError("WebViewAPI is not initialized")
        return api

    def _on_tool_continuation(self, answer_index: int) -> None:
        """Callback passed to ToolHandler for streaming continuation.

        This is called when the last tool execution completes, triggering
        a continuation of the conversation.

        Args:
            answer_index: Index of the answer being continued.
        """
        self._streaming_handler.continue_streaming(answer_index)

    def handle_user_message(self, message: str, images: list[str]) -> None:
        """Handle a user message submission.

        This is the entry point for user messages from the web UI.

        Args:
            message: The user's message text.
            images: List of image data strings (if any).
        """
        # Add question to chat state - single source of truth for answer index
        self._current_answer_index = self.chat_state.add_question(message)
        answer_index = self._current_answer_index

        # Notify API that a new Q/A turn is starting
        api = self._get_api()
        api.on_new_qa_turn(self.tab_id)

        # Store images if any
        if images:
            if hasattr(self.chat_state, "set_question_images"):
                self.chat_state.set_question_images(answer_index, list(images))
            elif hasattr(self.chat_state, "question_images"):
                while len(self.chat_state.question_images) <= answer_index:
                    self.chat_state.question_images.append([])
                self.chat_state.question_images[answer_index] = list(images)

        # Delegate to streaming handler
        self._streaming_handler.start(message, images, answer_index)

    def stop_streaming(self) -> None:
        """Stop the current streaming operation."""
        self._streaming_handler.stop()

    def on_streaming_complete(self) -> None:
        """Called when streaming completes successfully.

        This handles post-completion orchestration including:
        - Updating tab title from first question (fallback, only before summary exists)
        - Triggering summary generation if needed

        Note:
            api.on_streaming_end() is NOT called here because StreamProcessor
            already emits it when the done signal arrives. Calling it here would
            cause a double-fire.

            Streaming flags are reset by StreamingHandler before calling this method.
        """
        # Update title from first question only before summary has been generated.
        # Once summary is generated (or attempted), _generated is True and we stop
        # overwriting the title with the raw question on every subsequent message.
        if not self._summary_handler._generated:
            self._update_tab_title_from_question()

        # Trigger summary generation if this is the first answer
        self._summary_handler.trigger_if_needed()

        # One-shot post-streaming callback (e.g. handoff post-processing).
        # Must be cleared before calling so re-entrant streaming doesn't re-fire it.
        cb = self._on_streaming_complete_callback
        if cb is not None:
            self._on_streaming_complete_callback = None
            cb()

    def on_streaming_error(self, error: str) -> None:
        """Called when a streaming error occurs.

        Args:
            error: Error message describing what went wrong.
        """
        # Note: streaming flags are reset by StreamingHandler before calling this method

        # Notify UI (if available - don't raise if API is None)
        api = self._app_core.api
        if api is not None:
            api.on_error(self.tab_id, error)
            api.on_streaming_end(self.tab_id, self._current_answer_index)

    def _update_tab_title_from_question(self) -> None:
        """Update tab title from first question (fallback when no summary)."""
        title = self._summary_handler.get_title_from_first_question()
        if title:
            self.title = title
            api = self._app_core.api
            if api is not None:
                api.update_tab_title(self.tab_id, title)

    def cleanup_resources(self) -> None:
        """Clean up resources when tab is closed."""
        super().cleanup_resources()
        self.stop_streaming()

    # =======================================================================
    # Placeholder methods for API compatibility - to be implemented
    # =======================================================================

    def compact_conversation(self) -> dict[str, Any]:
        """Compact the conversation by removing tool call/result components.

        Returns:
            dict with 'compacted' bool and optional 'reason' string.
        """
        if self.is_streaming:
            return {"compacted": False, "reason": "streaming"}
        has_tools = any(a.has_tool_interactions() for a in self.chat_state.answers)
        if not has_tools:
            return {"compacted": False, "reason": "nothing_to_compact"}
        self.chat_state.compact_answers()
        return {"compacted": True}

    def truncate_conversation(self) -> dict[str, Any]:
        """Truncate conversation to last Q/A pair.

        Returns:
            dict with 'truncated' bool and optional 'reason' string.
        """
        if self.is_streaming:
            return {"truncated": False, "reason": "streaming"}
        truncated = self.chat_state.truncate_to_last()
        if truncated:
            return {"truncated": True}
        return {"truncated": False, "reason": "nothing_to_truncate"}

    def pop_conversation(self) -> dict[str, Any]:
        """Remove the last Q/A pair and return the popped question.

        Returns:
            dict with 'popped' bool, 'question' str, 'images' list on success.
        """
        if self.is_streaming:
            return {"popped": False, "reason": "streaming"}
        if not self.chat_state.questions:
            return {"popped": False, "reason": "empty"}
        last_question = self.chat_state.questions[-1]
        success, images = self.chat_state.pop_last_qa()
        if success:
            return {"popped": True, "question": last_question, "images": images}
        return {"popped": False, "reason": "failed"}

    def go_to_next_qa(self) -> None:
        """Navigate to next Q/A pair."""
        # Placeholder for navigation - not implemented in web version
        pass

    def go_to_previous_qa(self) -> None:
        """Navigate to previous Q/A pair."""
        # Placeholder for navigation - not implemented in web version
        pass

    def _find_pair_index_for_node(self, node_id: str) -> int | None:
        """Return the pair_index for a user or assistant node_id in the active path."""
        from conversation_graph import ConversationGraph

        if not isinstance(self.chat_state, ConversationGraph):
            return None
        for i, (user, asst) in enumerate(self.chat_state._get_active_pairs()):
            if user.id == node_id or (asst is not None and asst.id == node_id):
                return i
        return None

    def fork_from_node(self, node_id: str, new_question: str = "") -> dict[str, Any]:
        """Fork conversation from an assistant node with a new question."""
        from conversation_graph import ConversationGraph

        if not isinstance(self.chat_state, ConversationGraph):
            return {"success": False, "error": "Not a ConversationGraph"}
        if self.is_streaming:
            return {"success": False, "error": "Cannot fork while streaming"}
        if not new_question.strip():
            return {"success": False, "error": "Fork requires a non-empty question"}
        pair_index = self._find_pair_index_for_node(node_id)
        if pair_index is None:
            return {"success": False, "error": "Node not found in active path"}
        answer_index = self.chat_state.fork_question(pair_index, new_question)
        self._current_answer_index = answer_index
        api = self._get_api()
        api.on_new_qa_turn(self.tab_id)
        self._streaming_handler.start(new_question, [], answer_index)
        return {"success": True, "answer_index": answer_index}

    def regenerate_answer(self, node_id: str) -> dict[str, Any]:
        """Regenerate the answer for the pair containing node_id."""
        from conversation_graph import ConversationGraph

        if not isinstance(self.chat_state, ConversationGraph):
            return {"success": False, "error": "Not a ConversationGraph"}
        if self.is_streaming:
            return {"success": False, "error": "Cannot regenerate while streaming"}
        pair_index = self._find_pair_index_for_node(node_id)
        if pair_index is None:
            return {"success": False, "error": "Node not found in active path"}
        question = self.chat_state.questions[pair_index]
        images = (
            list(self.chat_state.question_images[pair_index])
            if pair_index < len(self.chat_state.question_images)
            else []
        )
        answer_index = self.chat_state.regenerate_answer(pair_index)
        self._current_answer_index = answer_index
        api = self._get_api()
        api.on_new_qa_turn(self.tab_id)
        self._streaming_handler.start(question, images, answer_index)
        return {"success": True, "answer_index": answer_index}

    def edit_question(self, node_id: str, new_text: str) -> dict[str, Any]:
        """Edit the question at the pair containing node_id and stream a new answer."""
        from conversation_graph import ConversationGraph

        if not isinstance(self.chat_state, ConversationGraph):
            return {"success": False, "error": "Not a ConversationGraph"}
        if self.is_streaming:
            return {"success": False, "error": "Cannot edit while streaming"}
        pair_index = self._find_pair_index_for_node(node_id)
        if pair_index is None:
            return {"success": False, "error": "Node not found in active path"}
        images = (
            list(self.chat_state.question_images[pair_index])
            if pair_index < len(self.chat_state.question_images)
            else []
        )
        answer_index = self.chat_state.edit_question(pair_index, new_text)
        self._current_answer_index = answer_index
        api = self._get_api()
        api.on_new_qa_turn(self.tab_id)
        self._streaming_handler.start(new_text, images, answer_index)
        return {"success": True, "answer_index": answer_index}
