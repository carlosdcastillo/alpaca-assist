"""UI-agnostic base class for ChatTab - no tkinter dependencies.

This class contains only business logic with no UI dependencies.
All UI interactions go through the WebViewAPI bridge.

Note on orphaned attributes:
    The following attributes have been removed as they are now owned by handler modules:
    - input_queue: Not needed (payload passed directly to StreamingHandler)
    - _tool_result_queue, _tool_call_queue: Dead code (never read)
    - _pending_tool_executions, _tool_execution_lock: Moved to ToolHandler
    - summary_generated, _summary_lock: Moved to SummaryHandler
    - current_request_thread: Moved to StreamingHandler
"""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING

from chat_state import ChatState
from conversation_graph import ConversationGraph
from core.tool_output_gate import cleanup_tab_output_dir
from tool_call_detector import ToolCallDetector

if TYPE_CHECKING:
    from core.app_core import AppCore

logger = logging.getLogger(__name__)


class ChatTabBase:
    """UI-agnostic base functionality for ChatTab.

    This class contains only business logic with no UI dependencies.
    All UI interactions go through the WebViewAPI bridge.
    """

    def __init__(
        self,
        tab_id: str,
        title: str,
        app_core: AppCore,
        conversation_id: int,
    ) -> None:
        self.tab_id = tab_id
        self.title = title
        self._app_core = app_core
        self.chat_state: ChatState | ConversationGraph = ConversationGraph()
        self.conversation_id: int = conversation_id
        self.preferences: dict[str, Any] = app_core.preferences

        # Streaming state (managed by StreamingHandler)
        self._streaming = False
        self.is_streaming: bool = False
        self._current_answer_index = -1
        self.stop_streaming_flag: threading.Event = threading.Event()
        self.content_update_queue: queue.Queue[Any] = queue.Queue()

        # Initialize tool detector (used by StreamProcessor)
        self.tool_detector: ToolCallDetector = ToolCallDetector()

        # Token usage metrics (populated by StreamProcessor from done chunks)
        self.last_invocation_metrics: dict[str, Any] | None = None
        self.session_output_tokens: int = 0
        self.session_input_tokens: int = 0

        # One-shot callback fired by on_streaming_complete (e.g. handoff post-processing).
        # Cleared after firing so it only runs once.
        self._on_streaming_complete_callback: Callable[[], None] | None = None

    def cleanup_resources(self) -> None:
        """Clean up resources when tab is closed."""
        self.stop_streaming_flag.set()
        while not self.content_update_queue.empty():
            try:
                self.content_update_queue.get_nowait()
            except queue.Empty:
                break
        cleanup_tab_output_dir(self.tab_id)

    def on_streaming_complete(self) -> None:
        """Called when streaming completes successfully.

        Subclasses should override this to handle post-completion logic.
        This base implementation is a no-op.
        """
        pass

    def on_streaming_error(self, error: str) -> None:
        """Called when a streaming error occurs.

        Subclasses should override this to handle streaming errors.
        This base implementation is a no-op.

        Args:
            error: Error message describing what went wrong.
        """
        pass

    def get_serializable_data(self) -> dict[str, Any]:
        """Get serializable data for session saving."""
        return {
            "chat_state": self.chat_state.to_dict(),
            "tab_id": self.tab_id,
            "name": self.title,
            "conversation_id": self.conversation_id,
        }

    def load_from_data(self, data: dict[str, Any]) -> None:
        """Load tab data from serialized form.

        conversation_id is intentionally not restored here — it is set at
        tab-creation time (via create_tab) so that clones and handoffs
        always get their own fresh ID.  Session restore passes the stored
        conversation_id directly to create_tab before calling this method.
        """
        chat_state_data = data.get("chat_state", {})

        if "graph" in chat_state_data:
            self.chat_state = ConversationGraph.from_dict(chat_state_data)
        else:
            self.chat_state = ChatState.from_dict(chat_state_data)

        self.title = data.get("name", data.get("title", "Chat"))
