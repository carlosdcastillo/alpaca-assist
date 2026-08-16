"""Streaming lifecycle management module for ChatTab.

This module handles streaming request lifecycle: start, stop, thread management,
and HTTP request preparation. It does not process content (delegated to StreamProcessor).

Key Classes:
    StreamingHandler: Manages streaming request lifecycle and HTTP communication.

Dependencies:
    - chat_tab_processor.StreamProcessor for content processing
    - chat_tab_tools.ToolHandler for continuation message preparation

Thread Safety:
    This module manages threading.Thread instances and uses ChatTabBase.stop_streaming_flag
    as the single stop Event (does NOT create a separate Event).
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any
from typing import TYPE_CHECKING

import requests

import utils

if TYPE_CHECKING:
    from core.chat_tab_base import ChatTabBase
    from core.chat_tab_processor import StreamProcessor
    from core.chat_tab_tools import ToolHandler

logger = logging.getLogger(__name__)


class StreamingHandler:
    """Manages streaming request lifecycle.

    This class handles:
    - Starting and stopping streaming requests
    - Thread management for streaming
    - HTTP request preparation (payload building, URL normalization)
    - Delegating content processing to StreamProcessor
    - Continuation after tool execution
    """

    # (connect_timeout, read_timeout): read_timeout is the maximum silence
    # between bytes on the open socket.  120s matches the watchdog's "stuck"
    # threshold in chat_tab_processor.py; a truly dead connection is closed
    # within 2 minutes instead of 30.  Genuinely slow models that produce
    # at least one byte per token are unaffected — tokens arrive far more
    # frequently than this in normal operation.
    STREAM_TIMEOUT: tuple[int, int] = (10, 120)

    def __init__(
        self,
        chat_tab: ChatTabBase,
        tool_handler: ToolHandler,
        processor: StreamProcessor | None = None,
    ) -> None:
        """Initialize the streaming handler.

        Args:
            chat_tab: The ChatTabBase instance for state access.
            tool_handler: ToolHandler for preparing continuation messages.
            processor: Optional StreamProcessor instance (created if not provided).
        """
        self._chat = chat_tab
        self._tool_handler = tool_handler

        # Import here to avoid circular import at module level
        from core.chat_tab_processor import StreamProcessor

        self._processor = processor or StreamProcessor(chat_tab, tool_handler)
        self._current_thread: threading.Thread | None = None
        self._current_response: requests.Response | None = None
        # Note: Uses chat_tab.stop_streaming_flag — do NOT create self._stop_flag

    def start(self, message: str, images: list[str], answer_index: int) -> None:
        """Start a new streaming request.

        Args:
            message: User message text.
            images: List of image data (if any).
            answer_index: Index from chat_state.add_question() for this turn.
        """
        # Set streaming flags
        self._chat._streaming = True
        self._chat.is_streaming = True

        # Clear stop flag
        self._chat.stop_streaming_flag.clear()

        # Notify UI that streaming has started
        api = self._chat._app_core.api
        if api is not None:
            api.on_streaming_start(self._chat.tab_id, answer_index)

        # Get available tools and the tab-specific system prompt.
        tools = self._chat._app_core.get_available_mcp_tools()
        system_prompt = self._chat._app_core.get_system_prompt(self._chat)

        # Prepare data payload
        chat_history_images: list[list[str]] = (
            list(self._chat.chat_state.question_images)
            if hasattr(self._chat.chat_state, "question_images")
            else []
        )

        # "llama3.1" was a leftover from an old local-Ollama setup and isn't
        # one of this app's actual models — fall back to the server's own
        # default instead of a name nothing here offers.
        from anthropic_ollama_server import DEFAULT_MODEL

        data_payload: dict[str, Any] = {
            "prompt": message,
            "model": self._chat.preferences.get("model", DEFAULT_MODEL),
            "chat_history_questions": self._chat.chat_state.questions.copy(),
            "chat_history_answers": [
                a.get_text_only_content() for a in self._chat.chat_state.answers
            ],
            "answer_index": answer_index,
            "prompt_images": images or [],
            "chat_history_images": chat_history_images,
        }

        # Start the streaming request thread
        self._current_thread = threading.Thread(
            target=self._fetch_response,
            args=(answer_index, data_payload, tools, system_prompt),
            daemon=True,
        )
        self._current_thread.start()
        logger.debug(f"[STREAMING] Started thread for answer {answer_index}")

    def stop(self) -> None:
        """Stop the current streaming operation."""
        logger.info("[STREAMING] Stopping streaming...")

        # Set stop flag on ChatTabBase (single source of truth)
        self._chat.stop_streaming_flag.set()

        # Close the underlying HTTP response to unblock any iter_lines() call
        # that is waiting for bytes on a dead or very slow connection.
        if self._current_response is not None:
            try:
                self._current_response.close()
            except Exception:
                pass

        # Reset streaming flags
        self._chat._streaming = False
        self._chat.is_streaming = False

        # Clear content queue
        while not self._chat.content_update_queue.empty():
            try:
                self._chat.content_update_queue.get_nowait()
            except queue.Empty:
                break

        # Finish streaming in chat state
        self._chat.chat_state.finish_streaming()

        # Notify UI
        api = self._chat._app_core.api
        if api is not None:
            api.on_streaming_end(self._chat.tab_id, self._chat._current_answer_index)

    def continue_streaming(self, answer_index: int) -> None:
        """Continue after tool execution (callback for ToolHandler).

        This is called by ToolHandler when the last tool execution completes.

        Args:
            answer_index: Index of the answer being continued.
        """
        logger.info(f"[CONTINUATION] Starting continuation for answer {answer_index}")

        try:
            # Re-enable streaming state
            self._chat._streaming = True
            self._chat.is_streaming = True
            logger.info("[CONTINUATION] Streaming state re-enabled")

            # Notify UI that streaming has started (critical for toolbar status)
            api = self._chat._app_core.api
            if api is not None:
                api.on_streaming_start(self._chat.tab_id, answer_index)

            # Get continuation messages from tool handler
            messages = self._tool_handler.prepare_continuation_messages(answer_index)
            logger.info(f"[CONTINUATION] Prepared {len(messages)} messages")

            # "llama3.1" was a leftover from an old local-Ollama setup and
            # isn't one of this app's actual models — fall back to the
            # server's own default instead of a name nothing here offers.
            from anthropic_ollama_server import DEFAULT_MODEL

            # Create continuation payload
            ollama_payload: dict[str, Any] = {
                "model": self._chat.preferences.get("model", DEFAULT_MODEL),
                "messages": messages,
                "stream": True,
                "conversation_id": self._chat.conversation_id,
            }

            # Add tools if available
            tools = self._chat._app_core.get_available_mcp_tools()
            if tools:
                ollama_payload["tools"] = tools
                logger.info(f"[CONTINUATION] Added {len(tools)} tools")

            system_prompt = self._chat._app_core.get_system_prompt(self._chat)
            if system_prompt:
                ollama_payload["system"] = system_prompt
            workspace_path = getattr(self._chat, "workspace_path", None)
            if workspace_path:
                ollama_payload["working_directory"] = workspace_path
            surface_socket = getattr(self._chat, "surface_socket", None)
            if surface_socket:
                ollama_payload["surface_socket"] = surface_socket

            # Make the continuation request
            api_url = self._chat.preferences.get(
                "api_url",
                "http://localhost:11434",
            )
            api_url = utils.normalize_api_url(api_url)
            full_url = f"{api_url}/api/chat"

            logger.info(f"[CONTINUATION] POST to: {full_url}")

            response = requests.post(
                full_url,
                json=ollama_payload,
                stream=True,
                timeout=self.STREAM_TIMEOUT,
            )
            self._current_response = response
            logger.info(f"[CONTINUATION] Response status: {response.status_code}")

            # Process the continuation stream
            logger.info("[CONTINUATION] Processing stream...")
            self._processor.process_stream(
                response,
                answer_index,
                self._chat.stop_streaming_flag,
            )
            logger.info("[CONTINUATION] Stream processing complete")

            # Reset streaming flags after completion
            self._chat._streaming = False
            self._chat.is_streaming = False

            # Call completion callback only if not stopped
            if not self._chat.stop_streaming_flag.is_set():
                self._chat.on_streaming_complete()

        except Exception as e:
            logger.exception(f"[CONTINUATION] Error: {e}")
            self._chat.on_streaming_error(str(e))
        finally:
            # Always reset streaming flags and clear thread reference
            # This mirrors _fetch_response's finally block
            self._chat._streaming = False
            self._chat.is_streaming = False
            self._current_response = None

    def _fetch_response(
        self,
        answer_index: int,
        data_payload: dict[str, Any],
        tools: list[dict[str, Any]] | None,
        system_prompt: str | None,
    ) -> None:
        """Execute HTTP request and hand response to processor.

        Args:
            answer_index: Index of the answer being generated.
            data_payload: Complete payload for the request (replaces input_queue pattern).
            tools: Available MCP tools for the request.
            system_prompt: Skills and project instructions (if any).

        Note:
            Streaming flags (_streaming, is_streaming) are reset here after
            process_stream() returns, before calling on_streaming_complete().
        """
        try:
            if self._chat.stop_streaming_flag.is_set():
                logger.debug(
                    f"[FETCH] Streaming stopped before request for answer {answer_index}",
                )
                return

            # Build messages via tool handler — same path as continuation requests
            # so tool history from previous turns is preserved consistently.
            # At this point the current answer slot is empty, so it contributes
            # only the user question (no partial tool state).
            messages = self._tool_handler.prepare_continuation_messages(answer_index)

            # Inject images for the current user turn if any
            prompt_images = data_payload.get("prompt_images", [])
            if prompt_images and messages and messages[-1].get("role") == "user":
                messages[-1] = {**messages[-1], "images": prompt_images}

            # "llama3.1" was a leftover from an old local-Ollama setup and
            # isn't one of this app's actual models — fall back to the
            # server's own default instead of a name nothing here offers.
            from anthropic_ollama_server import DEFAULT_MODEL

            # Create Ollama payload
            ollama_payload: dict[str, Any] = {
                "model": data_payload.get(
                    "model",
                    self._chat.preferences.get("model", DEFAULT_MODEL),
                ),
                "messages": messages,
                "stream": True,
                "conversation_id": self._chat.conversation_id,
            }

            # Add tools if available
            if tools:
                ollama_payload["tools"] = tools

            if system_prompt:
                ollama_payload["system"] = system_prompt
            workspace_path = getattr(self._chat, "workspace_path", None)
            if workspace_path:
                ollama_payload["working_directory"] = workspace_path
            surface_socket = getattr(self._chat, "surface_socket", None)
            if surface_socket:
                ollama_payload["surface_socket"] = surface_socket

            # Make the streaming request
            api_url = self._chat.preferences.get(
                "api_url",
                "http://localhost:11434",
            )
            api_url = utils.normalize_api_url(api_url)
            full_url = f"{api_url}/api/chat"

            logger.debug(f"[FETCH] Making request to: {full_url}")

            response = requests.post(
                full_url,
                json=ollama_payload,
                stream=True,
                timeout=self.STREAM_TIMEOUT,
            )
            self._current_response = response

            # Process the streaming response
            self._processor.process_stream(
                response,
                answer_index,
                self._chat.stop_streaming_flag,
            )

            # Reset streaming flags before calling completion callback
            # (matches continue_streaming pattern as documented in on_streaming_complete docstring)
            self._chat._streaming = False
            self._chat.is_streaming = False

            # Call completion callback only if not stopped
            if not self._chat.stop_streaming_flag.is_set():
                self._chat.on_streaming_complete()

        except Exception as e:
            logger.exception(f"[FETCH] Error: {e}")
            self._chat.on_streaming_error(str(e))
        finally:
            # Reset streaming flags (covers error path) and clear thread reference
            # On happy path these are already reset, but on error path this is essential
            self._chat._streaming = False
            self._chat.is_streaming = False
            self._current_thread = None
            self._current_response = None

    # Removed _normalize_api_url - now in utils.normalize_api_url
    # Removed _prepare_messages - superseded by tool_handler.prepare_continuation_messages
