"""Stream processing module for ChatTab.

This module handles SSE stream processing, content accumulation, tool call detection,
and content update queuing. It does not execute tools or make HTTP requests.

Key Classes:
    StreamProcessor: Processes SSE stream from Ollama and manages content updates.

Dependencies:
    Uses ToolHandler for tool call detection callbacks.

Thread Safety:
    This module reads the stop flag and populates the content queue.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from typing import TYPE_CHECKING

import requests

import utils

if TYPE_CHECKING:
    from core.chat_tab_base import ChatTabBase
    from core.chat_tab_tools import ToolHandler

logger = logging.getLogger(__name__)


class StreamProcessor:
    """Processes SSE stream content from Ollama.

    This class handles:
    - Parsing JSON lines from SSE stream
    - Accumulating content chunks
    - Detecting tool calls in content
    - Queuing ContentUpdate objects for UI
    """

    def __init__(
        self,
        chat_tab: ChatTabBase,
        tool_handler: ToolHandler | None = None,
    ) -> None:
        """Initialize the stream processor.

        Args:
            chat_tab: The ChatTabBase instance for state access.
            tool_handler: Optional ToolHandler for tool call execution.
        """
        self._chat = chat_tab
        self._tool_handler = tool_handler

    def process_stream(
        self,
        response: requests.Response,
        answer_index: int,
        stop_flag: Any,  # threading.Event
    ) -> None:
        """Process streaming response line by line.

        Args:
            response: The requests.Response object with iter_lines() generator.
            answer_index: Index of the answer being generated.
            stop_flag: threading.Event to check for stop requests.

        Emits api.on_streaming_end() when [DONE] signal received.

        Registers itself as a pending unit of work for the duration of the
        call (see ToolHandler.mark_stream_active) so that a fast tool call
        detected mid-stream can't trigger a continuation request before this
        stream has finished emitting any *further* tool calls it hasn't
        reached yet — without this, two process_stream() invocations can end
        up appending to the same answer concurrently, interleaving their
        content character-range by character-range.
        """
        if self._tool_handler:
            self._tool_handler.mark_stream_active()
        try:
            self._process_stream_inner(response, answer_index, stop_flag)
        finally:
            if self._tool_handler:
                self._tool_handler.mark_stream_finished(answer_index)

    def _process_stream_inner(
        self,
        response: requests.Response,
        answer_index: int,
        stop_flag: Any,  # threading.Event
    ) -> None:
        """Original process_stream body — see process_stream for the guard."""
        chunk_count = 0
        total_content = 0
        # Accumulate (tool_json, tc_store_id) pairs for call fold injection
        pending_call_folds: list[tuple[str, str]] = []
        done_received = False

        # Reject non-200 responses immediately so the error body is shown and
        # on_streaming_end is emitted correctly (the loop below only emits it
        # on the "done" signal — never on error bodies).
        if response.status_code != 200:
            error_body = (
                response.text[:500] if response.text else f"HTTP {response.status_code}"
            )
            logger.error(
                f"[STREAM] Non-200 response: {response.status_code}, body: {error_body}",
            )
            self._queue_content_update(
                answer_index,
                f"\n[Error {response.status_code}]: {error_body}",
                is_done=True,
                is_error=True,
            )
            api = self._chat._app_core.api
            if api is not None:
                api.on_streaming_end(self._chat.tab_id, answer_index)
            return

        # Watchdog: inject an inline warning if the stream goes quiet for
        # too long. iter_lines() blocks indefinitely; a separate thread is
        # the only way to interleave timed notifications without breaking
        # the streaming loop's own control flow.
        _last_chunk_ts: list[float] = [time.monotonic()]
        _watchdog_stop = threading.Event()

        def _watchdog() -> None:
            SLOW_SECS = 60
            STUCK_SECS = 120
            warned_slow = warned_stuck = False
            while not _watchdog_stop.wait(timeout=10.0):
                elapsed = time.monotonic() - _last_chunk_ts[0]
                if not warned_stuck and elapsed >= STUCK_SECS:
                    warned_stuck = True
                    self._queue_content_update(
                        answer_index,
                        f"\n\n*[No response for {STUCK_SECS}s — press Stop if the connection is stuck.]*\n",
                        is_done=False,
                    )
                elif not warned_slow and elapsed >= SLOW_SECS:
                    warned_slow = True
                    self._queue_content_update(
                        answer_index,
                        f"\n\n*[Connection is slow — still waiting after {SLOW_SECS}s...]*\n",
                        is_done=False,
                    )

        _watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        _watchdog_thread.start()

        try:
            for line in response.iter_lines(decode_unicode=True):
                _last_chunk_ts[0] = time.monotonic()  # heartbeat
                if stop_flag.is_set():
                    logger.debug(f"[STREAM] Stop flag set, breaking stream loop")
                    break

                if not line:
                    continue

                try:
                    data = json.loads(line.strip())
                    message = data.get("message", {})

                    # Handle Ollama native tool_calls field
                    if message.get("tool_calls"):
                        for tc in message["tool_calls"]:
                            fn = tc.get("function", {})
                            tool_name = fn.get("name", "")
                            arguments = fn.get("arguments", {})
                            tool_json = json.dumps(
                                {
                                    "tool_call": {
                                        "name": tool_name,
                                        "arguments": arguments,
                                    },
                                },
                            )
                            logger.debug(
                                f"[STREAM] Native tool_call detected: {tool_name}",
                            )
                            if self._tool_handler:
                                tc_store_id = self._tool_handler.handle_tool_call(
                                    tool_json,
                                    answer_index,
                                )
                                if tc_store_id:
                                    pending_call_folds.append((tool_json, tc_store_id))

                    if "content" in message:
                        content_chunk = message["content"]
                        chunk_count += 1
                        if content_chunk:
                            total_content += len(content_chunk)
                            logger.debug(
                                f"[STREAM] Chunk {chunk_count}: len={len(content_chunk)}, "
                                f"preview={content_chunk[:50]}",
                            )

                            # Check for tool calls and strip from content
                            content_chunk = self._handle_content_chunk(
                                content_chunk,
                                answer_index,
                                pending_call_folds,
                            )

                            # Persist content if there's any left after stripping
                            if content_chunk:
                                self._chat.chat_state.append_to_answer(
                                    answer_index,
                                    content_chunk,
                                )
                                self._queue_content_update(
                                    answer_index,
                                    content_chunk,
                                    is_done=False,
                                )
                        else:
                            logger.debug(f"[STREAM] Chunk {chunk_count}: empty")

                    if data.get("done", False):
                        done_received = True
                        logger.debug(
                            f"[STREAM] Done signal after {chunk_count} chunks, "
                            f"total={total_content}",
                        )

                        # Persist token metrics for webview_api.get_status_info().
                        # session_* fields accumulate across every call in this
                        # tab's lifetime (a tool loop alone can be dozens of
                        # calls) — last_invocation_metrics is the only place
                        # that reflects a single call in isolation.
                        metrics = data.get("invocation_metrics")
                        if metrics:
                            self._chat.last_invocation_metrics = metrics
                            self._chat.session_output_tokens += metrics.get(
                                "output_token_count",
                                0,
                            )
                            self._chat.session_input_tokens += metrics.get(
                                "input_token_count",
                                0,
                            )
                            self._chat.session_cached_input_tokens += metrics.get(
                                "cached_input_token_count",
                                0,
                            )

                        self._queue_content_update(answer_index, "", is_done=True)

                        # Inject call folds AFTER done signal
                        for tool_json, tc_store_id in pending_call_folds:
                            logger.debug(
                                f"[STREAM] Injecting call fold after done: {tc_store_id}",
                            )
                            if self._tool_handler:
                                self._tool_handler.inject_call_fold(
                                    answer_index,
                                    tool_json,
                                    tc_store_id,
                                )

                        # Emit streaming end signal via API
                        api = self._chat._app_core.api
                        if api is not None:
                            api.on_streaming_end(self._chat.tab_id, answer_index)
                        break

                except json.JSONDecodeError as e:
                    logger.debug(f"[STREAM] JSON decode error: {e}, line={line[:100]}")
                    continue

            # If the loop ended without a done signal, differentiate stop vs dropped connection.
            if not done_received:
                if stop_flag.is_set() and chunk_count > 0:
                    logger.info(
                        f"[STREAM] Stopped by user after {chunk_count} chunks",
                    )
                    stop_text = "\n\n*[Stopped by user]*"
                    self._chat.chat_state.append_to_answer(answer_index, stop_text)
                    self._queue_content_update(answer_index, stop_text, is_done=True)
                else:
                    if stop_flag.is_set():
                        logger.info(
                            f"[STREAM] Stop flag set before any content received",
                        )
                    else:
                        logger.warning(
                            f"[STREAM] Loop ended without done signal after {chunk_count} chunks",
                        )
                    self._queue_content_update(answer_index, "", is_done=True)
                api = self._chat._app_core.api
                if api is not None:
                    api.on_streaming_end(self._chat.tab_id, answer_index)

        except Exception as e:
            logger.error(f"[STREAM] Error processing stream: {e}")
            self._queue_content_update(
                answer_index,
                f"Stream error: {str(e)}",
                is_done=True,
                is_error=True,
            )
            api = self._chat._app_core.api
            if api is not None:
                api.on_streaming_end(self._chat.tab_id, answer_index)
        finally:
            _watchdog_stop.set()

    def _handle_content_chunk(
        self,
        content: str,
        answer_index: int,
        pending_call_folds: list[tuple[str, str]],
    ) -> str:
        """Process a single content chunk.

        Checks for tool calls in content, strips them, and queues for fold injection.

        Args:
            content: Content chunk to process.
            answer_index: Index of the answer.
            pending_call_folds: List to append (tool_json, tc_store_id) pairs to.

        Returns:
            Content with tool call JSON stripped, or original content if no tool call.
        """
        logger.debug(f"[TOOL CHECK] Checking: {content[:200]}...")

        # Check if content contains complete tool call JSON
        if self._chat.tool_detector.is_complete_json(content):
            logger.debug("[TOOL CHECK] Detected complete JSON, checking pattern")
            try:
                data = json.loads(content)
                if "tool_call" in data:
                    tool_call_data = data["tool_call"]
                    logger.debug(f"[TOOL CHECK] Found tool_call: {tool_call_data}")

                    # Re-serialize just the tool_call portion for handling
                    tool_json = json.dumps({"tool_call": tool_call_data})

                    # Send tool call to UI as special update
                    tool_call_update = utils.ContentUpdate(
                        answer_index=answer_index,
                        content_chunk=tool_json,
                        is_tool_call=True,
                        tool_id=tool_call_data.get("id", ""),
                    )
                    self._put_content_update_with_retry(tool_call_update)

                    api = self._chat._app_core.api
                    if api is not None:
                        api.on_content_update(self._chat.tab_id, tool_call_update)
                    logger.debug("[TOOL CHECK] Tool call sent to UI")

                    # Handle the tool call (execute it)
                    if self._tool_handler:
                        tc_store_id = self._tool_handler.handle_tool_call(
                            tool_json,
                            answer_index,
                        )
                        if tc_store_id:
                            pending_call_folds.append((tool_json, tc_store_id))
                            logger.debug(
                                f"[TOOL CHECK] Queued for fold injection: {tc_store_id}",
                            )

                    # Return empty string to strip tool call from content
                    return ""
                else:
                    logger.debug("[TOOL CHECK] No tool_call key in JSON")
            except json.JSONDecodeError as e:
                logger.debug(f"[TOOL CHECK] JSON decode error: {e}")
        else:
            logger.debug("[TOOL CHECK] Content is not complete JSON")

        return content

    def _queue_content_update(
        self,
        answer_index: int,
        content: str,
        is_done: bool,
        is_error: bool = False,
    ) -> None:
        """Queue a content update for the UI.

        Args:
            answer_index: Index of the answer.
            content: Content chunk or empty string.
            is_done: Whether this is the final update.
            is_error: Whether this is an error update.
        """
        update = utils.ContentUpdate(
            answer_index=answer_index,
            content_chunk=content,
            is_done=is_done,
            is_error=is_error,
        )
        self._put_content_update_with_retry(update)

        api = self._chat._app_core.api
        if api is not None:
            api.on_content_update(self._chat.tab_id, update)

    def _put_content_update_with_retry(
        self,
        update: Any,
        max_retries: int = 3,
    ) -> None:
        """Put a content update on the queue with retry logic.

        Uses utils.put_content_update_with_retry for centralized handling.

        Args:
            update: ContentUpdate to queue.
            max_retries: Maximum number of retry attempts.
        """
        utils.put_content_update_with_retry(
            self._chat.content_update_queue,
            update,
            max_retries,
        )
