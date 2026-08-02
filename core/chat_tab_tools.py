"""Tool handling module for ChatTab.

This module handles tool call detection, execution via MCP, result handling,
and conversation continuation after tool execution.

Key Classes:
    ToolHandler: Manages tool call lifecycle from detection to continuation.

Dependencies:
    Receives continuation callback from StreamingHandler to avoid circular imports.

Thread Safety:
    This module uses threading.Lock for pending execution tracking.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Any
from typing import TYPE_CHECKING

import utils
from core.tool_output_gate import gate_tool_call_arguments
from core.tool_output_gate import gate_tool_output

if TYPE_CHECKING:
    from core.chat_tab_base import ChatTabBase

logger = logging.getLogger(__name__)


class ToolHandler:
    """Handles tool call detection, execution, and continuation.

    This class manages the complete lifecycle of tool calls including:
    - Parsing tool call JSON (both nested and flat formats)
    - Executing tools via MCP in background threads
    - Handling tool results and persistence
    - Managing conversation continuation after tool execution
    """

    # Must comfortably exceed the longest timeout any tool legitimately
    # supports — run_shell_command alone allows up to shell_executor.MAX_TIMEOUT
    # (300s), and fetch_url_as_markdown can need curl_timeout plus a separate,
    # unbounded LLM summarization round-trip on top of it. A tool that
    # finishes within its own requested budget must never be cut off here
    # first; only a genuinely hung tool should ever hit this ceiling.
    TOOL_EXECUTION_TIMEOUT_SECONDS = 310.0

    # Coarse tool-result clearing: once a single turn's tool-call loop
    # exceeds this many call/result pairs, older pairs have their result
    # content replaced with a stub on subsequent continuation requests.
    # Bounds worst-case resend growth in long tool loops; see
    # TOOL_RESULT_CLEARING.md. Deliberately coarse and count-based (not
    # time-based) to avoid interacting with the prompt-cache TTL.
    KEEP_LAST_N_TOOL_PAIRS = 15
    CLEARED_TOOL_RESULT_STUB = (
        "[tool result cleared to reduce context size — result of this call "
        "is no longer shown; re-run the tool if you need this again]"
    )

    def __init__(
        self,
        chat_tab: ChatTabBase,
        continuation_callback: Callable[[int], None],
    ) -> None:
        """Initialize the tool handler.

        Args:
            chat_tab: The ChatTabBase instance for state access.
            continuation_callback: Callback to trigger streaming continuation
                after tool execution. Receives answer_index as argument.
        """
        self._chat = chat_tab
        self._continue = continuation_callback
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        # Set when handle_tool_call fires, cleared when continuation fires.
        # Gates _release_pending_unit so a tool-free turn never triggers a
        # continuation of its own already-complete answer (see that method).
        self._has_tool_calls = False

    def mark_stream_active(self) -> None:
        """Register the raw LLM stream itself as a pending unit of work.

        Without this, "last man standing" (see _execute_tool) can fire as
        soon as the tool calls detected *so far* finish, even though the
        stream that's still being read might be about to emit more tool
        calls it hasn't reached yet — a fast tool (e.g. get_time) can finish
        before the next SSE chunk even arrives over the network. Counting
        the stream's own lifetime alongside individual tool executions
        closes that race: continuation can't fire until the stream itself
        has also finished.
        """
        with self._pending_lock:
            self._pending_count += 1

    def mark_stream_finished(self, answer_index: int) -> None:
        """Release the stream's pending slot; fire continuation if last.

        Call exactly once when a process_stream() invocation ends, via
        whatever exit path (done signal, stop flag, or exception) — mirrors
        the "last man standing" pattern in _execute_tool's finally block.
        """
        self._release_pending_unit(answer_index)

    def _release_pending_unit(self, answer_index: int) -> None:
        """Decrement the shared pending counter; fire continuation if this
        was the last outstanding unit of work for a round that actually
        contained at least one tool call.

        A stream with zero tool calls needs no continuation — the model's
        answer is already complete on its own. mark_stream_active/finished
        bracket *every* stream, tool or not, so without the _has_tool_calls
        check, a plain tool-free turn would also hit "last man standing"
        the instant its own stream finished, firing a needless continuation
        that re-sends the now-complete answer — whose own stream then
        finishes and fires another, looping forever until the user hits
        stop.
        """
        with self._pending_lock:
            self._pending_count -= 1
            remaining = self._pending_count
            fire = remaining == 0 and self._has_tool_calls
            logger.debug(
                f"[TOOL] Pending unit released, remaining={remaining}, "
                f"has_tool_calls={self._has_tool_calls}, fire={fire}",
            )
            if fire:
                self._has_tool_calls = False
        if fire and not self._chat.stop_streaming_flag.is_set():
            self._continue(answer_index)

    def handle_tool_call(
        self,
        tool_json: str,
        answer_index: int,
    ) -> str | None:
        """Parse and execute a tool call.

        Args:
            tool_json: JSON string containing tool call data.
            answer_index: Index of the answer being generated.

        Returns:
            Stable tool ID (tc_store_id) for fold pairing, or None on error.
        """
        try:
            tool_data = json.loads(tool_json)

            # Handle nested format: {"tool_call": {"name": ..., "arguments": ...}}
            if "tool_call" in tool_data:
                tool_call_inner = tool_data["tool_call"]
                tool_name = tool_call_inner.get("name", "")
                arguments = tool_call_inner.get("arguments", {})
                tool_id = tool_call_inner.get("id", "")
            else:
                # Handle flat format: {"name": ..., "arguments": ...}
                tool_name = tool_data.get("name", "")
                arguments = tool_data.get("arguments", {})
                tool_id = tool_data.get("id", "")

            logger.debug(
                f"[TOOL] Handling tool call: {tool_name} with args: {arguments}",
            )

            # Parse server and tool name
            if "_" in tool_name:
                server_name, actual_tool_name = tool_name.split("_", 1)
            else:
                server_name = "default"
                actual_tool_name = tool_name

            logger.debug(
                f"[TOOL] Parsed server: {server_name}, tool: {actual_tool_name}",
            )

            # Build stable unique ID shared between TC and TR
            tc_store_id = (
                tool_id
                if tool_id
                else f"{server_name}_{actual_tool_name}_{uuid.uuid4().hex[:8]}"
            )

            # Persist tool call to ChatState. Gate oversized arguments before
            # storage — the call has already executed with the real,
            # unmodified arguments above; only the stored/replayed copy is
            # capped, since (unlike results) a tool_use_call block is never
            # stubbed by KEEP_LAST_N_TOOL_PAIRS regardless of age.
            gated_tool_json = gate_tool_call_arguments(
                tool_json,
                self._chat.tab_id,
                tc_store_id,
                tool_name,
            )
            self._chat.chat_state.add_tool_call_to_answer(
                answer_index,
                gated_tool_json,
                tc_store_id,
            )
            logger.debug(f"[TOOL] Tool call persisted with id={tc_store_id}")

            # Increment pending tool executions
            with self._pending_lock:
                self._pending_count += 1
                self._has_tool_calls = True
            logger.debug(f"[TOOL] Pending executions: {self._pending_count}")

            # Execute tool in background thread
            tool_thread = threading.Thread(
                target=self._execute_tool,
                args=(
                    server_name,
                    actual_tool_name,
                    arguments,
                    answer_index,
                    tc_store_id,
                ),
                daemon=True,
            )
            tool_thread.start()
            logger.debug("[TOOL] Tool execution thread started")
            return tc_store_id

        except Exception as e:
            logger.error(f"[TOOL] Error handling tool call: {e}")
            return None

    def _execute_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        answer_index: int,
        tool_id: str,
    ) -> None:
        """Execute a tool and handle the result.

        Uses "last man standing" pattern: whichever tool thread is last to
        finish (pending count reaches 0) fires exactly ONE continuation request.

        Args:
            server_name: Name of the MCP server.
            tool_name: Name of the tool to execute.
            arguments: Tool arguments dictionary.
            answer_index: Index of the answer being generated.
            tool_id: Stable ID for tool call/result pairing.
        """
        logger.debug(
            f"[TOOL] Executing: {server_name}/{tool_name} id={tool_id}",
        )
        result: Any = None
        callback_event = threading.Event()

        def callback(res: Any) -> None:
            nonlocal result
            result = res
            callback_event.set()
            logger.debug(f"[TOOL] Callback received result: {res}")

        try:
            if server_name == "internal":
                import internal_tools

                logger.debug(f"[TOOL] Calling internal tool: {tool_name}")
                result = internal_tools.call_tool(tool_name, arguments)
                callback(result)
            else:
                logger.debug("[TOOL] Calling MCP tool via app_core")
                self._chat._app_core.call_mcp_tool(
                    server_name,
                    tool_name,
                    arguments,
                    callback,
                )

            # Wait for result with timeout (using Event instead of busy-wait)
            if server_name != "internal" and not callback_event.wait(
                timeout=self.TOOL_EXECUTION_TIMEOUT_SECONDS,
            ):
                logger.warning(f"[TOOL] Tool execution timed out: {tool_id}")
                result = None

            logger.debug(
                f"[TOOL] Execution completed, result is None: {result is None}",
            )

            if result:
                # Format and persist result
                display_text = self._format_result(result)
                result_str = (
                    json.dumps(result, indent=2)
                    if isinstance(result, dict)
                    else str(result)
                )

                # Gate oversized results out of the model context (and the fold
                # UI) — full content goes to a temp file, a preview stays inline.
                gated_result_str = gate_tool_output(
                    result_str,
                    self._chat.tab_id,
                    tool_id,
                    tool_name,
                )
                if gated_result_str != result_str:
                    display_text = gated_result_str
                result_str = gated_result_str

                self._chat.chat_state.add_tool_result_to_answer(
                    answer_index,
                    result_str,
                    tool_id,
                )
                logger.debug(f"[TOOL] Result persisted with id={tool_id}")

                # Send result to UI
                result_update = utils.ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=result_str,
                    is_tool_result=True,
                )
                self._put_content_update(result_update)

                api = self._chat._app_core.api
                if api is not None:
                    api.on_content_update(self._chat.tab_id, result_update)
                logger.debug("[TOOL] Result sent to UI")

                # Inject result fold
                fold_id = f"fold-result-{answer_index}-{tool_id}"
                self._inject_result_fold(answer_index, display_text, fold_id)

                # Wait for JavaScript to confirm fold rendering
                logger.debug(f"[TOOL] Waiting for fold {fold_id}...")
                api = self._chat._app_core.api
                if api is not None:
                    rendered = api.wait_for_fold_rendered(
                        self._chat.tab_id,
                        fold_id,
                        timeout=2.0,
                    )
                    if rendered:
                        logger.debug(f"[TOOL] Fold {fold_id} rendered")
                    else:
                        logger.warning(f"[TOOL] Timeout waiting for {fold_id}")
            else:
                logger.warning(f"[TOOL] Tool execution timed out: {tool_id}")
                timeout_secs = int(self.TOOL_EXECUTION_TIMEOUT_SECONDS)
                error_msg = (
                    f"Tool **{tool_name}** (server: {server_name}) "
                    f"timed out after {timeout_secs}s with no response."
                )
                self._chat.chat_state.add_tool_result_to_answer(
                    answer_index,
                    error_msg,
                    tool_id,
                )
                fold_id = f"fold-result-{answer_index}-{tool_id}"
                self._inject_result_fold(answer_index, error_msg, fold_id)

        except Exception as e:
            logger.error(f"[TOOL] Error executing tool: {e}")
            error_msg = (
                f"Tool **{tool_name}** (server: {server_name}) " f"raised an error: {e}"
            )
            self._chat.chat_state.add_tool_result_to_answer(
                answer_index,
                error_msg,
                tool_id,
            )
            fold_id = f"fold-result-{answer_index}-{tool_id}"
            self._inject_result_fold(answer_index, error_msg, fold_id)
        finally:
            logger.debug(f"[TOOL] Execution finished for {tool_id}")
            self._release_pending_unit(answer_index)

    def _format_result(self, result: Any) -> str:
        """Format tool result for display.

        Args:
            result: Raw tool result (dict, list, or other).

        Returns:
            Formatted string for fold display.
        """
        if isinstance(result, dict):
            # MCP-style result with content array (check before single-key fallback)
            if "content" in result and isinstance(result["content"], list):
                texts: list[str] = []
                for item in result["content"]:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", ""))
                if texts:
                    return "\n".join(texts)

            # Simple result with single key (string/scalar content)
            if len(result) == 1:
                if "result" in result:
                    return str(result["result"])
                if "content" in result:
                    return str(result["content"])

        # Default: JSON representation
        return json.dumps(result, indent=2) if isinstance(result, dict) else str(result)

    def _put_content_update(self, update: Any) -> None:
        """Put a content update on the queue with retry logic.

        Uses utils.put_content_update_with_retry for centralized handling.

        Args:
            update: ContentUpdate to queue.
        """
        utils.put_content_update_with_retry(
            self._chat.content_update_queue,
            update,
            max_retries=3,
        )

    def _inject_result_fold(
        self,
        answer_index: int,
        body_text: str,
        fold_id: str | None = None,
    ) -> None:
        """Inject a tool result fold widget.

        Args:
            answer_index: Index of the answer.
            body_text: Formatted result text for the fold body.
            fold_id: Optional fold ID (auto-generated if not provided).
        """
        if fold_id is None:
            fold_id = f"fold-result-{answer_index}-{threading.current_thread().ident}"

        api = self._chat._app_core.api
        if api is not None:
            api.inject_tool_fold(
                self._chat.tab_id,
                fold_id,
                "result",
                body_text,
                answer_index,
            )

    def inject_call_fold(
        self,
        answer_index: int,
        tool_json: str,
        tc_store_id: str,
    ) -> None:
        """Inject a tool call fold widget.

        This is a semi-public interface called by StreamProcessor to inject
        tool call folds after the streaming done signal. It is not purely
        private because it crosses class boundaries, but it is not fully
        public as it should only be called by the stream processing pipeline.

        Args:
            answer_index: Index of the answer.
            tool_json: Tool call JSON for the fold body.
            tc_store_id: Stable ID for fold pairing.
        """
        fold_id = f"fold-call-{answer_index}-{tc_store_id}"
        api = self._chat._app_core.api
        if api is not None:
            api.inject_tool_fold(
                self._chat.tab_id,
                fold_id,
                "call",
                tool_json,
                answer_index,
            )

    def prepare_continuation_messages(
        self,
        answer_index: int,
    ) -> list[dict[str, Any]]:
        """Prepare messages for continuation request including tool call and result.

        Iterates FullAnswer components in chronological order to build correctly
        ordered message history:
          - str before first ToolCall -> pre-tool assistant message
          - ToolCall -> tool_use_call message
          - str between ToolCall and ToolResult -> SKIP (progress indicators)
          - ToolResult -> tool_result message
          - str after last ToolResult -> post-tool assistant message

        Args:
            answer_index: Index of the answer being continued.

        Returns:
            List of message dictionaries for Ollama API.
        """
        from chat_state import ToolCall, ToolResult

        # Get chat history
        questions = self._chat.chat_state.questions.copy()
        answers = self._chat.chat_state.answers.copy()
        _qi = getattr(self._chat.chat_state, "question_images", None)
        all_images: list[list[str]] = list(_qi) if isinstance(_qi, list) else []

        # First pass: pair up each turn's tool calls/results (unchanged logic),
        # and also flatten them into one chronological, conversation-wide list.
        # Clearing decisions below are made against that flat list rather than
        # per turn, so a conversation with many turns that each only have a
        # few tool calls still gets bounded overall — previously only the
        # turn currently being continued was ever considered, so accumulated
        # history across completed turns was never cleared at all, no matter
        # how large the conversation got. See TOOL_RESULT_CLEARING.md.
        per_turn_pairs: list[list[tuple[Any, Any]]] = []
        all_pairs: list[tuple[Any, Any]] = []
        for i, (q, a) in enumerate(zip(questions, answers)):
            if i > answer_index:
                break
            tc_list: list[Any] = []
            tr_by_id: dict[str, Any] = {}
            for component in a.components:
                if isinstance(component, ToolCall):
                    tc_list.append(component)
                elif isinstance(component, ToolResult):
                    tr_by_id[component.id] = component
            tool_pairs: list[tuple[Any, Any]] = []
            used_ids: set[str] = set()
            for tc in tc_list:
                tr = tr_by_id.get(tc.id)
                if tr and tc.id not in used_ids:
                    tool_pairs.append((tc, tr))
                    used_ids.add(tc.id)
            per_turn_pairs.append(tool_pairs)
            all_pairs.extend(tool_pairs)

        # Pairs never get *removed* from the kept-full set once past the
        # global boundary below except by aging further back as more pairs
        # accumulate, and stubbed content only ever grows by appending
        # identical stub text — so the cleared region stays cache-stable
        # across calls exactly as it did in the single-turn version.
        n_total = len(all_pairs)
        global_clear_boundary = max(0, n_total - self.KEEP_LAST_N_TOOL_PAIRS)
        keep_full_ids = {tc.id for tc, _tr in all_pairs[global_clear_boundary:]}
        last_cleared_id = (
            all_pairs[global_clear_boundary - 1][0].id
            if global_clear_boundary > 0
            else None
        )

        # Second pass: build the actual message list in chronological order,
        # using the global full/stub membership computed above instead of a
        # per-turn boundary.
        messages: list[dict[str, Any]] = []
        for i, (q, a) in enumerate(zip(questions, answers)):
            if i > answer_index:
                break

            if not q.strip():
                continue

            user_msg: dict[str, Any] = {"role": "user", "content": q}
            imgs = all_images[i] if i < len(all_images) else []
            if imgs:
                user_msg["images"] = imgs
            messages.append(user_msg)

            pre_texts: list[str] = []
            post_texts: list[str] = []
            seen_tc = False
            for component in a.components:
                if isinstance(component, str):
                    if not seen_tc:
                        pre_texts.append(component)
                    else:
                        post_texts.append(component)
                elif isinstance(component, ToolCall):
                    seen_tc = True
                    post_texts = []  # Reset post-text on new ToolCall

            # Pre-tool assistant text
            pre_text = "".join(pre_texts).strip()
            if pre_text:
                messages.append({"role": "assistant", "content": pre_text})

            # Tool call / result pairs, coarsely cleared against the global
            # (conversation-wide) boundary computed above — bounds resend
            # growth whether it comes from one long single-turn tool loop or
            # from many turns each with a handful of tool calls. See
            # TOOL_RESULT_CLEARING.md.
            for tc, tr in per_turn_pairs[i]:
                call_data = self._parse_for_message(tc.content, tc.id)
                if call_data:
                    messages.append(
                        {
                            "role": "tool_use_call",
                            "content": "data",
                            "call": call_data,
                        },
                    )
                    if tc.id in keep_full_ids:
                        result_content = f"Tool execution result:\n{tr.content}"
                    else:
                        result_content = self.CLEARED_TOOL_RESULT_STUB
                    result_msg: dict[str, Any] = {
                        "role": "tool_result",
                        "content": result_content,
                        "id": tc.id,
                    }
                    # Second cache breakpoint: the cleared region only ever
                    # grows by appending identical stub content, so marking
                    # its end lets later continuation requests keep hitting
                    # cache against it instead of paying full price for it
                    # on every round-trip.
                    if tc.id == last_cleared_id:
                        result_msg["cache_control"] = True
                    messages.append(result_msg)

            # Post-tool continuation text
            post_text = "".join(post_texts).strip()
            if post_text:
                messages.append({"role": "assistant", "content": post_text})

            # Mark end of last complete historical turn as cache breakpoint
            if i == answer_index - 1 and messages:
                messages[-1] = {**messages[-1], "cache_control": True}

        return messages

    def _parse_for_message(
        self,
        tool_call_content: str,
        tool_id: str,
    ) -> dict[str, Any] | None:
        """Parse tool call content to extract structured call data.

        Args:
            tool_call_content: JSON string with tool call data.
            tool_id: Stable ID for the tool call.

        Returns:
            Dictionary with call data or None on parse failure.
        """
        try:
            parsed = json.loads(tool_call_content)

            # Check for {"tool_call": {"name": "...", "arguments": {...}}} format
            if "tool_call" in parsed:
                tool_call = parsed["tool_call"]
                return {
                    "id": tool_id,
                    "name": tool_call.get("name", "unknown_tool"),
                    "arguments": tool_call.get("arguments", {}),
                }

            # Check for direct {"name": "...", "arguments": {...}} format
            if "name" in parsed:
                return {
                    "id": tool_id,
                    "name": parsed.get("name", "unknown_tool"),
                    "arguments": parsed.get("arguments", {}),
                }
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

        # Fallback: minimal data
        return {"id": tool_id, "name": "unknown_tool", "arguments": {}}
