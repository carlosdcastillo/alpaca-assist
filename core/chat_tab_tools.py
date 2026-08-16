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
import time
import uuid
from collections.abc import Callable
from typing import Any
from typing import TYPE_CHECKING

import image_tool_result
import utils
from core import timing
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

    # Coarse tool-result clearing: pairs older than this byte budget (walked
    # backward from the most recent, conversation-wide) have their result
    # content replaced with a stub on subsequent continuation requests.
    # Bounds worst-case resend growth. Bytes, not pair count — real pair
    # sizes vary by 100x+ (a few dozen bytes for a read_file_range call vs.
    # tens of KB for a gated write_file result), so a fixed count gives
    # wildly inconsistent actual resend cost depending on which tools
    # happened to be recent; a byte budget bounds the thing that actually
    # matters. Originally 24KB (smaller than either per-item gate
    # threshold), but real tool-loop conversations routinely produce
    # individual results in the tens-to-hundreds-of-KB range (shell
    # command output, read_file) — at 24KB that meant clearing, and the
    # cache-prefix invalidation that comes with it kicked in after
    # essentially one kept pair.
    # 256KB leaves room for dozens of full-size pairs before eviction
    # starts, trading a larger worst-case first-time resend for far fewer
    # clear-driven cache busts. Deliberately size-based (not time-based)
    # to avoid interacting with the prompt-cache TTL.
    KEEP_TOOL_CONTEXT_BUDGET_BYTES = 256 * 1024

    # Image results (internal_view_image) are exempt from the byte budget
    # above and kept by count instead — a single downscaled screenshot
    # still typically encodes to several times the whole text budget by
    # itself, so counting it there would evict everything around it on the
    # very next call. 2
    # matches the before/after-screenshot pattern this tool is actually
    # used for.
    KEEP_LAST_N_IMAGES = 2

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

        Draining to zero *without* firing is also the one moment that means
        "the whole turn is over", so the turn's timing is closed here. The
        obvious-looking hook, on_streaming_complete, is per-invocation — it
        runs once for the initial call and once more for every continuation —
        so closing there would stop the clock at the end of the first LLM
        call and drop every tool and continuation that followed.
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
        continuing = fire and not self._chat.stop_streaming_flag.is_set()
        if continuing:
            self._continue(answer_index)
        elif remaining == 0:
            # Nothing outstanding and nothing to continue into: the turn is
            # over, whether it ended cleanly or was stopped. StreamingHandler
            # .stop also finalises, and whichever gets there first wins —
            # finalize_turn_timing drops the timing object once it has run.
            self._chat.finalize_turn_timing(answer_index)

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
            # stubbed by the byte-budget clearing below regardless of age.
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
                started_at=time.time(),
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

        # Tool duration is measured around dispatch only.  It deliberately
        # stops before _inject_result_fold and its wait_for_fold_rendered
        # block below — that wait is UI sync (and up to 2s of pure timeout
        # when the user has switched tabs), not work the tool did.
        exec_started_at = time.time()
        exec_mono = time.monotonic()
        duration_ms = 0

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

            duration_ms = int((time.monotonic() - exec_mono) * 1000)
            logger.debug(
                f"[TOOL] Execution completed in {duration_ms}ms, "
                f"result is None: {result is None}",
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
                    started_at=exec_started_at,
                    duration_ms=duration_ms,
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
                self._inject_result_fold(
                    answer_index,
                    display_text,
                    fold_id,
                    duration_ms,
                )

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
                    started_at=exec_started_at,
                    duration_ms=duration_ms,
                )
                fold_id = f"fold-result-{answer_index}-{tool_id}"
                self._inject_result_fold(
                    answer_index,
                    error_msg,
                    fold_id,
                    duration_ms,
                )

        except Exception as e:
            logger.error(f"[TOOL] Error executing tool: {e}")
            duration_ms = int((time.monotonic() - exec_mono) * 1000)
            error_msg = (
                f"Tool **{tool_name}** (server: {server_name}) " f"raised an error: {e}"
            )
            self._chat.chat_state.add_tool_result_to_answer(
                answer_index,
                error_msg,
                tool_id,
                started_at=exec_started_at,
                duration_ms=duration_ms,
            )
            fold_id = f"fold-result-{answer_index}-{tool_id}"
            self._inject_result_fold(answer_index, error_msg, fold_id, duration_ms)
        finally:
            logger.debug(f"[TOOL] Execution finished for {tool_id} in {duration_ms}ms")
            # Attribute the execution to the turn from the finally block so a
            # timeout or a raised tool still counts toward the turn's tool_ms —
            # time spent waiting on a tool that failed is time the user waited.
            turn_timing = self._chat.current_turn_timing
            if turn_timing is not None:
                turn_timing.add_tool(duration_ms)
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
        duration_ms: int | None = None,
    ) -> None:
        """Inject a tool result fold widget.

        Args:
            answer_index: Index of the answer.
            body_text: Formatted result text for the fold body.
            fold_id: Optional fold ID (auto-generated if not provided).
            duration_ms: How long the tool ran, shown in the fold header.
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
                duration_ms=duration_ms,
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
        # how large the conversation got.
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

        # Walk backward from the most recent pair, accumulating actual byte
        # size (call args + result) until KEEP_TOOL_CONTEXT_BUDGET_BYTES is
        # exceeded — everything from that point forward stays full,
        # everything before it gets cleared. The single most recent pair
        # always stays full regardless of its own size, so the model never
        # loses direct visibility into what it just did. Pairs never get
        # *removed* from the kept-full set except by aging further back as
        # more pairs accumulate, and stubbed content only ever grows by
        # appending identical stub text — so the cleared region stays
        # cache-stable across calls exactly as it did in the pair-count
        # version.
        #
        # Image results (internal_view_image, see image_tool_result.py) are
        # deliberately invisible to this walk — skipped entirely rather than
        # counted. A single downscaled screenshot still typically encodes to
        # several times the whole text budget by itself, so counting it here
        # would blow the budget out in one step and stub every *other* pair
        # around it too, including small, unrelated, otherwise-easily-kept
        # text results. Images get their own, independent retention rule
        # below instead.
        n_total = len(all_pairs)
        budget = self.KEEP_TOOL_CONTEXT_BUDGET_BYTES
        text_keep_ids: set[str] = set()
        newest_text_seen = False
        for idx in range(n_total - 1, -1, -1):
            tc, tr = all_pairs[idx]
            if image_tool_result.parse_image_result(tr.content) is not None:
                continue
            is_newest_text = not newest_text_seen
            newest_text_seen = True
            pair_bytes = len(tc.content.encode("utf-8")) + len(tr.content.encode("utf-8"))
            if not is_newest_text and pair_bytes > budget:
                break
            budget -= pair_bytes
            text_keep_ids.add(tc.id)

        # Images get their own small keep-count, independent of the text
        # budget above, so a screenshot stays visible for a few turns after
        # loading instead of vanishing the moment it's no longer the single
        # newest pair (which, given its size, is exactly what the shared
        # byte-budget walk would otherwise do to it immediately). 2 matches
        # the before/after-screenshot pattern this tool actually gets used
        # for in practice.
        image_keep_ids: set[str] = set()
        images_kept = 0
        for idx in range(n_total - 1, -1, -1):
            tc, tr = all_pairs[idx]
            if image_tool_result.parse_image_result(tr.content) is None:
                continue
            if images_kept >= self.KEEP_LAST_N_IMAGES:
                break
            image_keep_ids.add(tc.id)
            images_kept += 1

        keep_full_ids = text_keep_ids | image_keep_ids
        # Cache breakpoint: the most recent pair, of either kind, that's
        # actually cleared. A kept image is exactly as stable call-to-call
        # as a stub is (identical bytes each time, until it eventually ages
        # out), so it's safe on either side of this marker — the marker
        # only needs to sit after the last thing that could still be
        # changing, not at a clean single cutpoint in the pair sequence.
        last_cleared_id = None
        for idx in range(n_total - 1, -1, -1):
            tc, _tr = all_pairs[idx]
            if tc.id not in keep_full_ids:
                last_cleared_id = tc.id
                break

        # Per-turn wall-clock, used to prefix user messages that follow a real
        # pause.  Both live on the conversation model; a conversation saved
        # before timing existed yields empty lists and simply gets no markers.
        _qt = getattr(self._chat.chat_state, "question_times", None)
        question_times: list[Any] = list(_qt) if isinstance(_qt, list) else []
        _tt = getattr(self._chat.chat_state, "turn_timings", None)
        turn_timings: list[Any] = list(_tt) if isinstance(_tt, list) else []
        previous_turn_end: float | None = None

        # Second pass: build the actual message list in chronological order,
        # using the global full/stub membership computed above instead of a
        # per-turn boundary.
        messages: list[dict[str, Any]] = []
        for i, (q, a) in enumerate(zip(questions, answers)):
            if i > answer_index:
                break

            if not q.strip():
                continue

            marker = timing.history_time_marker(
                question_times[i] if i < len(question_times) else None,
                previous_turn_end,
                is_first=not messages,
            )
            user_msg: dict[str, Any] = {"role": "user", "content": marker + q}
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
            # from many turns each with a handful of tool calls.
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

            # The gap the next question is measured against is when this turn
            # stopped producing output — not when it was asked. Using the
            # question time would fold the model's own working time into what
            # reads as the user's thinking time.
            turn = turn_timings[i] if i < len(turn_timings) else None
            completed = turn.get("completed_at") if isinstance(turn, dict) else None
            previous_turn_end = completed or timing.iso_to_epoch(
                question_times[i] if i < len(question_times) else None,
            )

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
