import concurrent.futures
import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import requests

from chat_tab_streaming_core import ChatTabStreamingCore
from expansion_language import expand
from tool_progress_manager import ToolProgressManager
from utils import ContentUpdate

BASE_URL: str = "http://localhost:11434/api/chat"
WHITESPACE_PATTERN = re.compile("\\s+")
MARKDOWN_SYMBOLS_PATTERN = re.compile("[*_`#-]+")
NEWLINES_PATTERN = re.compile("\\n+")
MULTIPLE_NEWLINES_PATTERN = re.compile("\\n{3,}")
TOOL_RESULT_PATTERN = re.compile(
    "\\*\\*Tool \\d+ Result:\\*\\*\\n```\\n(.*?)\\n```",
    re.DOTALL,
)
OPENAI_TOOL_CALLS_PATTERN = re.compile(
    '\\{\\s*"tool_calls"\\s*:\\s*\\[.*?\\]\\s*\\}',
    re.DOTALL,
)
PROMPT_PATTERN = re.compile("/prompt:(\\w+)")


class ChatTabStreamingAdvanced(ChatTabStreamingCore):
    """Streaming functionality for ChatTab - Advanced tool execution, connection management, and UI interactions."""

    def fetch_api_response(self, answer_index: int) -> None:
        """Fetch API response for a specific answer index using queue-based updates."""
        try:
            if self.stop_streaming_flag.is_set():
                print(f"Streaming stopped before API request for answer {answer_index}")
                return
            self.parent.check_mcp_status()
            data_payload: dict[str, Any] = self.input_queue.get(timeout=3)
            if data_payload is None:
                return
            payload_answer_index = data_payload.get("answer_index")
            if payload_answer_index != answer_index:
                print(
                    f"Warning: Answer index mismatch. Expected {answer_index}, got {payload_answer_index}",
                )
                return
            if "messages" in data_payload:
                messages = data_payload["messages"]
            else:
                messages: list[dict[str, str]] = []
                for q, a in zip(
                    data_payload["chat_history_questions"],
                    data_payload["chat_history_answers"],
                ):
                    if q.strip() and a.strip():
                        expanded_q = expand(q)
                        messages.append({"role": "user", "content": expanded_q})
                        (
                            assistant_content,
                            tool_results,
                            jsons,
                        ) = self._extract_tool_results_from_content(a)
                        if assistant_content.strip():
                            messages.append(
                                {"role": "assistant", "content": assistant_content},
                            )
                        for tool_result, js in zip(tool_results, jsons):
                            messages.append(
                                {
                                    "role": "tool_use_call",
                                    "content": "data",
                                    "call": js["tool_call"],
                                },
                            )
                            if "id" in js["tool_call"]:
                                messages.append(
                                    {
                                        "role": "tool_result",
                                        "content": f"Tool execution result:\n{tool_result}",
                                        "id": js["tool_call"]["id"],
                                    },
                                )
                            else:
                                messages.append(
                                    {
                                        "role": "tool_result",
                                        "content": f"Tool execution result:\n{tool_result}",
                                    },
                                )
                messages.append({"role": "user", "content": data_payload["prompt"]})
            available_tools = self.parent.get_available_mcp_tools()
            ollama_payload: dict[str, Any] = {
                "model": data_payload["model"],
                "messages": messages,
                "stream": True,
            }
            if available_tools:
                ollama_payload["tools"] = available_tools
            print(f"Starting API request for answer index {answer_index}")
            print(f"Tools included: {len(available_tools)} tools")
            print(f"Messages: {len(messages)} total")
            self._process_streaming_response(
                ollama_payload,
                answer_index,
                is_continuation=False,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(
                f"Unexpected error in fetch_api_response for answer index {answer_index}: {e}",
            )
            import traceback

            traceback.print_exc()
            if not self.stop_streaming_flag.is_set():
                error_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=error_msg,
                    is_done=True,
                    is_error=True,
                )
                self._put_content_update_with_retry(error_update)
        finally:
            print(f"API request thread ending for answer index {answer_index}")
            self.is_streaming = False
            self.current_request_thread = None

    def _detect_complete_tool_call_in_stream(self, accumulated_content: str) -> bool:
        """
        Detect if we have a complete tool call in the accumulated content.
        Returns True if a complete tool call is found.
        """
        try:
            if '"tool_call"' not in accumulated_content:
                return False
            tool_positions = self._find_internal_tool_calls(accumulated_content)
            if tool_positions:
                print(
                    f"Found complete tool call during streaming at positions: {tool_positions}",
                )
                return True
            openai_positions, _ = self._find_and_convert_openai_tool_calls(
                accumulated_content,
            )
            if openai_positions:
                print(f"Found OpenAI format tool call during streaming")
                return True
            return False
        except Exception as e:
            print(f"Error detecting tool call in stream: {e}")
            return False

    def _execute_all_tool_calls_with_connection(
        self,
        text: str,
        response: requests.Response | None = None,
    ) -> list[str]:
        """Execute all tool calls found in the text with connection management."""
        with self._tool_execution_lock:
            self._pending_tool_executions += 1
        try:
            tool_call_positions, modified_text = self._find_tool_calls(text)
            results = []
            print(f"Found {len(tool_call_positions)} tool call(s) to execute")

            def execute_single_tool_with_connection(i, start_pos, end_pos):
                """Execute a single tool with connection management."""
                print(f"Executing tool call {i + 1} at positions {start_pos}-{end_pos}")
                tool_response = response if i == 0 else None
                result = self._execute_tool_call_with_connection_management(
                    modified_text,
                    start_pos,
                    end_pos,
                    tool_response,
                )
                return (i, result if result else f"Tool call {i + 1} execution failed")

            if tool_call_positions:
                try:
                    results_dict = {}
                    for i, (start_pos, end_pos) in enumerate(tool_call_positions):
                        try:
                            idx, result = execute_single_tool_with_connection(
                                i,
                                start_pos,
                                end_pos,
                            )
                            results_dict[idx] = result
                        except Exception as e:
                            print(f"Tool execution failed: {e}")
                            results_dict[i] = f"Tool execution error: {str(e)}"
                    for i in sorted(results_dict.keys()):
                        results.append(results_dict[i])
                except Exception as e:
                    print(f"Error in tool execution: {e}")
                    results.append(f"Tool execution error: {str(e)}")
            return results
        finally:
            with self._tool_execution_lock:
                self._pending_tool_executions = max(
                    0,
                    self._pending_tool_executions - 1,
                )

    def _execute_tool_call_with_connection_management(
        self,
        text: str,
        start_pos: int,
        end_pos: int,
        managed_response: requests.Response | None = None,
    ) -> str | None:
        """Execute a specific tool call with enhanced connection management."""
        try:
            json_str = text[start_pos:end_pos]
            json_str_clean = WHITESPACE_PATTERN.sub(" ", json_str)
            json_variants = [json_str, json_str_clean]
            if json_str_clean.endswith("}") and json_str_clean.count(
                "{",
            ) > json_str_clean.count("}"):
                json_variants.append(json_str_clean + "}")
                print("Added missing closing brace to tool call JSON for execution")
            tool_call_data = None
            for variant in json_variants:
                try:
                    tool_call_data = json.loads(variant)
                    break
                except json.JSONDecodeError:
                    continue
            if tool_call_data is None:
                print(f"Failed to parse tool call JSON after cleaning attempts")
                return f"Error parsing tool call JSON: Could not parse after cleaning"
            if "tool_call" not in tool_call_data:
                return "Error: Invalid tool call format"
            tool_call = tool_call_data["tool_call"]
            tool_name = tool_call["name"]
            arguments = tool_call.get("arguments", {})
            print(f"Executing tool: {tool_name} with args: {arguments}")
            try:
                from enhanced_tool_progress_manager import (
                    ConnectionAwareToolProgressManager,
                )

                connection_id = (
                    f"tool_{tool_name}_{int(time.time())}" if managed_response else None
                )
                progress_manager = ConnectionAwareToolProgressManager(
                    tool_name,
                    self,
                    connection_id,
                )
                progress_manager.start(managed_response)
            except ImportError:
                from tool_progress_manager import ToolProgressManager

                progress_manager = ToolProgressManager(tool_name, self)
                progress_manager.start()
            try:
                if "_" in tool_name:
                    server_name, actual_tool_name = tool_name.split("_", 1)
                else:
                    progress_manager.error("Invalid tool name format")
                    return f"Error: Invalid tool name format: {tool_name}"
                print(f"Server name: {server_name}, Tool name: {actual_tool_name}")
                mcp_manager = getattr(self.parent, "mcp_manager", None)
                main_loop = getattr(self.parent, "event_loop", None)
                if mcp_manager and main_loop:
                    try:
                        import asyncio

                        future = asyncio.run_coroutine_threadsafe(
                            mcp_manager.call_tool(
                                server_name,
                                actual_tool_name,
                                arguments,
                            ),
                            main_loop,
                        )
                        max_wait = 300
                        check_interval = 0.1
                        elapsed = 0.0
                        last_progress_update = 0.0
                        progress_update_interval = 2.0
                        try:
                            result = future.result(timeout=0.1)
                            progress_manager.complete()
                            return (
                                json.dumps(result, indent=2)
                                if result
                                else "Tool execution completed"
                            )
                        except TimeoutError:
                            pass
                        while elapsed < max_wait:
                            if future.done():
                                try:
                                    result = future.result(timeout=0.01)
                                    progress_manager.complete()
                                    return (
                                        json.dumps(result, indent=2)
                                        if result
                                        else "Tool execution completed"
                                    )
                                except Exception as e:
                                    progress_manager.error(str(e))
                                    return f"Error executing tool: {str(e)}"
                            if (
                                elapsed - last_progress_update
                                >= progress_update_interval
                            ):
                                progress_manager.update_progress(elapsed, max_wait)
                                last_progress_update = elapsed
                            time.sleep(check_interval)
                            elapsed += check_interval
                        future.cancel()
                        progress_manager.timeout(max_wait)
                        return f"Tool execution timed out after {max_wait} seconds"
                    except Exception as e:
                        progress_manager.error(str(e))
                        return f"Error executing tool: {str(e)}"
                else:
                    progress_manager.error("MCP Manager not available")
                    return "MCP Manager not available"
            finally:
                progress_manager.cleanup()
        except Exception as e:
            return f"Error parsing tool call: {str(e)}"

    def _incremental_highlight(self):
        """Perform highlighting in chunks to avoid UI freezing."""
        try:
            content = self.chat_display.get("1.0", tk.END)
            last_pos = getattr(self.chat_display, "last_highlighted_position", "1.0")
            if hasattr(self.chat_display, "highlight_text_from_position"):
                self.chat_display.highlight_text_from_position(last_pos)
            else:
                self.chat_display.highlight_text()
            self.chat_display.last_highlighted_position = self.chat_display.index(
                tk.END + " -1c",
            )
            print("Incremental highlighting completed")
        except Exception as e:
            print(f"Error in incremental highlighting: {e}")
            try:
                self.chat_display.highlight_text()
            except:
                pass

    def stop_streaming(self) -> None:
        """Stop the current streaming request."""
        print("Stopping streaming...")
        self.stop_streaming_flag.set()
        self._stop_processor()
        if self.current_request_thread and self.current_request_thread.is_alive():
            print(
                f"Attempting to stop current request thread: {self.current_request_thread}",
            )
        with self._continuation_lock:
            keys_to_remove = [
                key
                for key in self._continuation_states.keys()
                if key.startswith("continuation_started_")
            ]
            for key in keys_to_remove:
                del self._continuation_states[key]
            if keys_to_remove:
                print(f"Cleared {len(keys_to_remove)} continuation states on stop")
        with self._tool_execution_lock:
            if self._pending_tool_executions > 0:
                print(
                    f"Clearing {self._pending_tool_executions} pending tool executions",
                )
                self._pending_tool_executions = 0
        while not self.content_update_queue.empty():
            try:
                self.content_update_queue.get_nowait()
            except queue.Empty:
                break
        if self.chat_state.is_streaming():
            current_answer_index = len(self.chat_state.answers) - 1
            if current_answer_index >= 0:
                stop_message = "\n\n[Streaming stopped by user]"
                self.chat_state.append_to_answer(current_answer_index, stop_message)
                self.chat_display.set_server_mode(True)
                self.chat_display.config(state=tk.NORMAL)
                self._insert_content_at_answer(current_answer_index, stop_message)
                self.chat_display.set_server_mode(False)
                self.chat_display.config(state=tk.NORMAL)
        self.chat_state.finish_streaming()
        self.is_streaming = False
        print(f"Force reset is_streaming to False")
        self.update_submit_button_text()
        self.current_request_thread = None
        self.parent.master.after(100, self._final_highlight)

    def _post_process_content(
        self,
        accumulated_content: str,
        answer_index: int,
    ) -> None:
        """Post-process accumulated content to check for OpenAI format tool calls."""
        try:
            if not self.is_streaming:
                print(
                    f"Skipping post-process - not actively streaming (answer {answer_index})",
                )
                return
            if self.stop_streaming_flag.is_set():
                print(
                    f"Skipping post-process - stop flag is set (answer {answer_index})",
                )
                return
            tool_positions, modified_text = self._find_and_convert_openai_tool_calls(
                accumulated_content,
            )
            if tool_positions:
                print(
                    f"Found OpenAI format tool calls in completed content, executing...",
                )
                if self.is_streaming and (not self.stop_streaming_flag.is_set()):
                    self._handle_tool_calls_and_continue(modified_text, answer_index)
                else:
                    print(f"Skipping tool execution - not streaming or stop flag set")
        except Exception as e:
            print(f"Error in post-processing content: {e}")

    def _make_continuation_request(self, answer_index: int) -> None:
        """Make a continuation request after tool calls have been executed."""
        try:
            print(f"Making continuation request for answer {answer_index}")
            if self.stop_streaming_flag.is_set():
                print("Continuation cancelled - stop flag set")
                return
            self.is_streaming = True
            print(
                f"Reset is_streaming to True for continuation (answer {answer_index})",
            )
            questions, answers, _ = self.chat_state.get_safe_copy_full()
            if answer_index >= len(questions) or answer_index >= len(answers):
                print(f"Invalid answer_index {answer_index} for continuation")
                done_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk="",
                    is_done=True,
                    is_error=False,
                )
                self._put_content_update_with_retry(done_update)
                return
            messages: list[dict[str, str]] = []
            for i, (q, a) in enumerate(zip(questions, answers)):
                a = a.get_text_content()
                if i <= answer_index and q.strip():
                    messages.append({"role": "user", "content": expand(q)})
                    if a.strip():
                        (
                            assistant_content,
                            tool_results,
                            jsons,
                        ) = self._extract_tool_results_from_content(a)
                        if assistant_content.strip():
                            messages.append(
                                {"role": "assistant", "content": assistant_content},
                            )
                        for tool_result, js in zip(tool_results, jsons):
                            messages.append(
                                {
                                    "role": "tool_use_call",
                                    "content": "data",
                                    "call": js["tool_call"],
                                },
                            )
                            if "id" in js["tool_call"]:
                                messages.append(
                                    {
                                        "role": "tool_result",
                                        "content": f"Tool execution result:\n{tool_result}",
                                        "id": js["tool_call"]["id"],
                                    },
                                )
                            else:
                                messages.append(
                                    {
                                        "role": "tool_result",
                                        "content": f"Tool execution result:\n{tool_result}",
                                    },
                                )
                elif i > answer_index:
                    break
            print(f"Prepared {len(messages)} messages for continuation")
            selected_model = self.parent.get_selected_model()
            continuation_payload = {
                "model": selected_model,
                "messages": messages,
                "stream": True,
            }
            available_tools = self.parent.get_available_mcp_tools()
            if available_tools:
                continuation_payload["tools"] = available_tools
                print(f"Added {len(available_tools)} tools to continuation request")
            else:
                print("WARNING: No tools available for continuation request")
            self.current_request_thread = threading.current_thread()
            print(
                f"Updated current_request_thread for continuation: {self.current_request_thread}",
            )
            self._process_streaming_response(
                continuation_payload,
                answer_index,
                is_continuation=True,
            )
        except Exception as e:
            error_msg = f"Unexpected error in continuation: {str(e)}"
            print(f"{error_msg}")
            import traceback

            traceback.print_exc()
            self.is_streaming = False
            error_update = ContentUpdate(
                answer_index=answer_index,
                content_chunk=f"\n\n[{error_msg}]",
                is_done=True,
                is_error=True,
            )
            self._put_content_update_with_retry(error_update)

    def _replace_openai_tool_calls_in_display(self, answer_index: int) -> None:
        """Thread-safe version that ensures UI operations run on main thread."""
        if threading.current_thread() != threading.main_thread():
            self.parent.master.after_idle(
                lambda: self._replace_openai_tool_calls_in_display(answer_index),
            )
            return
        try:
            self.chat_display.config(state=tk.NORMAL)
            full_content = self.chat_display.get("1.0", tk.END)
            lines = full_content.split("\n")
            answer_count = 0
            answer_start_line = None
            answer_end_line = None
            for i, line in enumerate(lines):
                if line.startswith("A: ") or (
                    answer_start_line is not None and line.startswith("A:")
                ):
                    if answer_count == answer_index:
                        answer_start_line = i
                    elif answer_count == answer_index + 1:
                        answer_end_line = i
                        break
                    answer_count += 1
            if answer_start_line is None:
                print(f"Could not find answer {answer_index} in display")
                return
            if answer_end_line is None:
                answer_end_line = len(lines)
            answer_lines = lines[answer_start_line:answer_end_line]
            answer_text = "\n".join(answer_lines)
            modified = False

            def replace_openai_format(match):
                nonlocal modified
                try:
                    openai_json = match.group(0)
                    openai_data = json.loads(openai_json)
                    if "tool_calls" in openai_data and isinstance(
                        openai_data["tool_calls"],
                        list,
                    ):
                        for tool_call in openai_data["tool_calls"]:
                            if "function" in tool_call:
                                function = tool_call["function"]
                                tool_name = function.get("name", "")
                                arguments_str = function.get("arguments", "{}")
                                try:
                                    arguments = json.loads(arguments_str)
                                except:
                                    arguments = {}
                                internal_format = {
                                    "tool_call": {
                                        "name": tool_name,
                                        "arguments": arguments,
                                    },
                                }
                                modified = True
                                return json.dumps(internal_format, indent=2)
                    return match.group(0)
                except:
                    return match.group(0)

            new_answer_text = OPENAI_TOOL_CALLS_PATTERN.sub(
                replace_openai_format,
                answer_text,
            )
            if modified:
                start_pos = f"{answer_start_line + 1}.0"
                end_pos = f"{answer_end_line + 1}.0"
                self.chat_display.delete(start_pos, end_pos)
                self.chat_display.insert(
                    start_pos,
                    new_answer_text + "\n"
                    if answer_end_line < len(lines)
                    else new_answer_text,
                )
                print(
                    f"Replaced OpenAI tool call format with internal format in answer {answer_index}",
                )
                self.chat_display.highlight_text()
            self.chat_display.config(state=tk.DISABLED)
        except Exception as e:
            print(f"Error replacing OpenAI tool calls in display: {e}")
            import traceback

            traceback.print_exc()
            self.chat_display.config(state=tk.DISABLED)

    def process_content_queue(self) -> None:
        """Process queue with smart highlighting throttling and proper termination."""
        with self._processor_lock:
            if not self._queue_processor_running:
                print("Queue processor stopping - flag is False")
                return
        updates_processed = 0
        streaming_finished = False
        content_accumulated = 0
        last_highlight_time = time.time()
        has_pending_updates = False
        chars_since_newline = getattr(self, "_chars_since_last_newline", 0)
        NEWLINE_THRESHOLD = 900000
        HIGHLIGHT_MIN_INTERVAL = 0.2
        HIGHLIGHT_CONTENT_THRESHOLD = 100
        HIGHLIGHT_UPDATE_THRESHOLD = 10
        MAX_UPDATES_PER_CYCLE = 10
        CYCLE_TIME_LIMIT = 0.05
        cycle_start_time = time.time()
        updates_this_cycle = 0
        try:
            while True:
                if updates_this_cycle >= MAX_UPDATES_PER_CYCLE:
                    print(
                        f"Reached update limit ({MAX_UPDATES_PER_CYCLE}), yielding control",
                    )
                    break
                if time.time() - cycle_start_time > CYCLE_TIME_LIMIT:
                    print(
                        f"Cycle time limit reached ({CYCLE_TIME_LIMIT}s), yielding control",
                    )
                    break
                try:
                    update = self.content_update_queue.get(timeout=0.001)
                    has_pending_updates = True
                    updates_this_cycle += 1
                    if update.is_error:
                        error_content = f"\n\n[Error: {update.content_chunk}]"
                        self.chat_state.append_to_answer(
                            update.answer_index,
                            error_content,
                        )
                        self.chat_state.finish_streaming()
                        content_to_insert = error_content
                        streaming_finished = True
                        chars_since_newline = 0
                    else:
                        original_content = update.content_chunk
                        content_to_insert = self._add_newlines_to_long_content(
                            original_content,
                            chars_since_newline,
                            NEWLINE_THRESHOLD,
                        )
                        if "\n" in content_to_insert:
                            last_newline_pos = content_to_insert.rfind("\n")
                            chars_since_newline = (
                                len(content_to_insert) - last_newline_pos - 1
                            )
                        else:
                            chars_since_newline += len(content_to_insert)
                        self.chat_state.append_to_answer(
                            update.answer_index,
                            content_to_insert,
                        )
                        if update.is_done:
                            self.chat_state.finish_streaming()
                            streaming_finished = True
                    self._chars_since_last_newline = chars_since_newline
                    self._insert_content_at_answer(
                        update.answer_index,
                        content_to_insert,
                    )
                    updates_processed += 1
                    content_accumulated += len(content_to_insert)
                    current_time = time.time()
                    time_since_last_highlight = current_time - last_highlight_time
                    is_tool_indicator = (
                        "⚡" in content_to_insert or "🔧" in content_to_insert
                    )
                    is_code_block = "```" in content_to_insert
                    should_highlight = (
                        time_since_last_highlight >= HIGHLIGHT_MIN_INTERVAL
                        or content_accumulated >= HIGHLIGHT_CONTENT_THRESHOLD
                        or updates_processed >= HIGHLIGHT_UPDATE_THRESHOLD
                        or update.is_done
                        or is_tool_indicator
                        or is_code_block
                    )
                    if should_highlight:
                        self.chat_display.highlight_text()
                        last_highlight_time = current_time
                        content_accumulated = 0
                        updates_processed = 0
                    if (
                        update.is_done
                        and update.answer_index == 0
                        and (not self.summary_generated)
                    ):
                        self.parent.master.after(3000, self.get_summary)
                    if streaming_finished:
                        break
                except queue.Empty:
                    break
            current_time = time.time()
            if updates_processed > 0 or content_accumulated > 0:
                self.parent.master.after(10, lambda: self.chat_display.highlight_text())
            if streaming_finished and (not self._has_pending_tool_execution()):
                print("Streaming finished and no pending tool executions - finishing")
                self._finish_streaming()
                return
            with self._processor_lock:
                if self._queue_processor_running:
                    if (
                        has_pending_updates
                        and updates_this_cycle >= MAX_UPDATES_PER_CYCLE
                    ):
                        delay = 10
                    elif has_pending_updates or self._has_pending_tool_execution():
                        delay = 50
                    else:
                        delay = 200
                    self.parent.master.after(delay, self.process_content_queue)
                else:
                    print("Queue processor stopping - manually stopped")
        except Exception as e:
            print(f"Error in queue processor: {e}")
            import traceback

            traceback.print_exc()
            self._finish_streaming()

    def _extract_tool_results_from_content(
        self,
        content: str,
    ) -> tuple[str, list[str], list[dict]]:
        """
        Extract tool results from content and return clean assistant content + tool results + tool call JSONs.
        Tool results should be formatted as user messages in the conversation.
        Returns: (clean_content, tool_results, tool_call_jsons)
        """
        if not content:
            return (content, [], [])
        tool_results = []
        tool_call_jsons = []
        clean_content = content
        for match in TOOL_RESULT_PATTERN.finditer(content):
            tool_result = match.group(1).strip()
            if tool_result:
                tool_results.append(tool_result)
        clean_content = TOOL_RESULT_PATTERN.sub("", clean_content)
        tool_positions, _ = self._find_tool_calls(content)
        for start_pos, end_pos in tool_positions:
            try:
                json_str = content[start_pos:end_pos]
                json_str_clean = re.sub("\\s+", " ", json_str)
                tool_call_data = json.loads(json_str_clean)
                if "tool_call" in tool_call_data:
                    tool_call_jsons.append(tool_call_data)
            except json.JSONDecodeError:
                try:
                    json_str_fixed = json_str_clean + "}"
                    tool_call_data = json.loads(json_str_fixed)
                    if "tool_call" in tool_call_data:
                        tool_call_jsons.append(tool_call_data)
                except:
                    pass
        openai_positions, modified_text = self._find_and_convert_openai_tool_calls(
            content,
        )
        if openai_positions:
            for start_pos, end_pos in openai_positions:
                try:
                    json_str = modified_text[start_pos:end_pos]
                    json_str_clean = re.sub("\\s+", " ", json_str)
                    tool_call_data = json.loads(json_str_clean)
                    if "tool_call" in tool_call_data:
                        tool_call_jsons.append(tool_call_data)
                except:
                    pass
        clean_content = self._filter_tool_calls_from_content(clean_content)
        clean_content = MULTIPLE_NEWLINES_PATTERN.sub("\n\n", clean_content).strip()
        return (clean_content, tool_results, tool_call_jsons)

    def _insert_content_at_answer(self, answer_index: int, content: str) -> None:
        """Thread-safe version that ensures UI operations run on main thread."""
        if not content:
            return
        if threading.current_thread() != threading.main_thread():
            self.parent.master.after_idle(
                lambda: self._insert_content_at_answer(answer_index, content),
            )
            return
        self.chat_display.set_server_mode(True)
        self.chat_display.config(state=tk.NORMAL)
        if answer_index in self.answer_end_positions:
            insert_pos = self.answer_end_positions[answer_index]
        else:
            insert_pos = self._find_answer_position(answer_index)
        self.chat_display.insert(insert_pos, content)
        self.answer_end_positions[answer_index] = f"{insert_pos} + {len(content)}c"
        if self._was_at_bottom():
            try:
                current_xview = self.chat_display.xview()
                self.chat_display.yview_moveto(1.0)
                if current_xview:
                    self.chat_display.xview_moveto(current_xview[0])
            except tk.TclError:
                self.chat_display.see(tk.END)
        self.chat_display.set_server_mode(False)

    def submit_message(self) -> str:
        """Submit the message to the API and handle the response."""
        if self.is_streaming:
            self.stop_streaming()
            return "break"
        was_intelligent_wrap_active = False
        try:
            if hasattr(self.parent, "intelligent_wrapper"):
                was_intelligent_wrap_active = (
                    self.parent.intelligent_wrapper.is_widget_wrapped(self.chat_display)
                )
                if was_intelligent_wrap_active:
                    print("Intelligent wrap was active, deactivating for streaming...")
                    self.parent.intelligent_wrapper.remove_intelligent_wrap(
                        self.chat_display,
                    )
                    self._was_intelligent_wrap_active_before_submit = True
                else:
                    self._was_intelligent_wrap_active_before_submit = False
            else:
                self._was_intelligent_wrap_active_before_submit = False
        except Exception as e:
            print(f"Error checking intelligent wrap state: {e}")
            self._was_intelligent_wrap_active_before_submit = False
        user_input = self.input_field.get("1.0", tk.END).strip()
        if not user_input:
            return "break"
        expanded_input = expand(user_input)
        if hasattr(self.parent, "prompt_manager"):

            def expand_prompt(match):
                trigger = match.group(1)
                prompt = self.parent.prompt_manager.get_prompt_by_trigger(trigger)
                if prompt:
                    return prompt.body
                else:
                    return match.group(0)

            expanded_input = PROMPT_PATTERN.sub(expand_prompt, expanded_input)
        self.hide_autocomplete_menu()
        answer_index = self.chat_state.add_question(expanded_input)
        self.input_field.delete("1.0", tk.END)
        if answer_index > 0:
            separator = "-" * 80
            self._insert_structural_content(f"\n{separator}\n\n")
        self._insert_structural_content(f"Q: {expanded_input}\n")
        self._insert_structural_content(f"A:\n")
        self.answer_end_positions[answer_index] = self.chat_display.index(
            tk.END + " -1c",
        )
        selected_model = self.parent.get_selected_model()
        questions, answers, _ = self.chat_state.get_safe_copy_full()
        data_payload = {
            "prompt": expanded_input,
            "model": selected_model,
            "chat_history_questions": questions,
            "chat_history_answers": [x.get_text_content() for x in answers],
            "answer_index": answer_index,
        }
        self.input_queue.put(data_payload)
        self.is_streaming = True
        self.stop_streaming_flag.clear()
        self.update_submit_button_text()
        self._start_processor_if_needed()
        self.current_request_thread = threading.Thread(
            target=self.fetch_api_response,
            args=(answer_index,),
            daemon=True,
        )
        self.current_request_thread.start()
        return "break"

    def _handle_tool_calls_with_managed_connection(
        self,
        accumulated_content: str,
        answer_index: int,
        response: requests.Response | None,
        connection_id: str,
    ) -> None:
        """Execute tool calls while managing the HTTP connection."""
        try:
            if not self.is_streaming:
                print(
                    f"WARNING: Attempted to handle tool calls with connection when not streaming (answer {answer_index})",
                )
                return
            if self.stop_streaming_flag.is_set():
                print(
                    f"WARNING: Attempted to handle tool calls when stop flag is set (answer {answer_index})",
                )
                return

            def execute_and_continue():
                try:
                    if not self.is_streaming or self.stop_streaming_flag.is_set():
                        print(
                            "Tool execution cancelled - not streaming or stop flag set",
                        )
                        if response:
                            self._graceful_connection_close(response)
                        return
                    if response and self.connection_manager:
                        try:
                            from enhanced_tool_progress_manager import (
                                ConnectionAwareToolProgressManager,
                            )

                            progress_manager = ConnectionAwareToolProgressManager(
                                "connection_manager",
                                self,
                                connection_id,
                            )
                            self.connection_manager.register_connection(
                                connection_id,
                                response,
                                progress_manager,
                            )
                            print(
                                f"Registered connection {connection_id} for management",
                            )
                        except ImportError:
                            print(
                                "Enhanced connection manager not available, using standard approach",
                            )
                    tool_positions, _ = self._find_tool_calls(accumulated_content)
                    tool_call_ids = []
                    for i, (start_pos, end_pos) in enumerate(tool_positions):
                        try:
                            json_str = accumulated_content[start_pos:end_pos]
                            json_str_clean = re.sub("\\s+", " ", json_str)
                            tool_call_data = json.loads(json_str_clean)
                            if "tool_call" in tool_call_data:
                                tool_call = tool_call_data["tool_call"]
                                tool_id = tool_call.get(
                                    "id",
                                    f"tool_{i}_{int(time.time())}",
                                )
                                tool_call_ids.append(tool_id)
                                tool_call_json = json.dumps(tool_call_data, indent=2)
                                self.chat_state.add_tool_call_to_answer(
                                    answer_index,
                                    tool_call_json,
                                    tool_id,
                                )
                                print(f"Added ToolCall with ID {tool_id} to chat state")
                        except Exception as e:
                            print(f"Error adding tool call to chat state: {e}")
                            tool_call_ids.append(f"tool_{i}_error")
                    tool_results = self._execute_all_tool_calls_with_connection(
                        accumulated_content,
                        response,
                    )
                    if self.stop_streaming_flag.is_set():
                        print("Stopping tool execution - stop flag set")
                        return
                    for i, tool_result in enumerate(tool_results):
                        if tool_result:
                            tool_id = (
                                tool_call_ids[i]
                                if i < len(tool_call_ids)
                                else f"tool_{i}_result"
                            )
                            try:
                                data_result = json.loads(tool_result)
                                if (
                                    isinstance(data_result, dict)
                                    and "content" in data_result
                                ):
                                    if (
                                        isinstance(data_result["content"], list)
                                        and len(data_result["content"]) > 0
                                    ):
                                        value = data_result["content"][0].get(
                                            "text",
                                            str(data_result),
                                        )
                                    else:
                                        value = str(data_result["content"])
                                else:
                                    value = json.dumps(data_result, indent=2)
                            except (json.JSONDecodeError, KeyError, TypeError):
                                value = str(tool_result)
                            self.chat_state.add_tool_result_to_answer(
                                answer_index,
                                value,
                                tool_id,
                            )
                            print(f"Added ToolResult with ID {tool_id} to chat state")
                            result_text = (
                                f"\n\n**Tool {i + 1} Result:**\n```\n{value}\n```"
                            )
                            update = ContentUpdate(
                                answer_index=answer_index,
                                content_chunk=result_text,
                                is_done=False,
                                is_error=False,
                            )
                            if not self._put_content_update_with_retry(update):
                                print(f"Failed to queue tool result {i + 1}")
                            if i > 0:
                                time.sleep(0.05)
                    print(
                        f"Tool execution completed for answer {answer_index}, starting continuation...",
                    )
                    if response and self.connection_manager:
                        try:
                            self.connection_manager.release_connection(connection_id)
                            print(
                                f"Released connection {connection_id} from management",
                            )
                        except:
                            pass
                    if response:
                        self._graceful_connection_close(response)
                    continuation_thread = threading.Thread(
                        target=self._continue_after_tool_calls,
                        args=(answer_index, tool_results),
                        daemon=True,
                    )
                    time.sleep(1)
                    continuation_thread.start()
                except Exception as tool_error:
                    print(f"Error executing tool calls: {tool_error}")
                    import traceback

                    traceback.print_exc()
                    if response and self.connection_manager:
                        try:
                            self.connection_manager.release_connection(connection_id)
                        except:
                            pass
                    if response:
                        self._graceful_connection_close(response)
                    error_text = f"\n\n**Tool Execution Error:** {str(tool_error)}"
                    error_update = ContentUpdate(
                        answer_index=answer_index,
                        content_chunk=error_text,
                        is_done=True,
                        is_error=True,
                    )
                    self._put_content_update_with_retry(error_update)

            tool_thread = threading.Thread(target=execute_and_continue, daemon=True)
            tool_thread.start()
        except Exception as e:
            print(f"Error in connection-managed tool execution: {e}")
            if response:
                self._graceful_connection_close(response)

    def _is_complete_json_object(self, text: str) -> bool:
        """
        Check if the text contains a complete JSON object for a tool call.
        Returns True if the JSON is complete and valid.
        """
        if not text or '{"tool_call"' not in text:
            return False
        start_idx = text.find('{"tool_call"')
        if start_idx == -1:
            start_idx = text.find('{ "tool_call"')
            if start_idx == -1:
                start_idx = text.find('{\n  "tool_call"')
                if start_idx == -1:
                    return False
        json_candidate = text[start_idx:]
        brace_count = 0
        in_string = False
        escape_next = False
        for i, char in enumerate(json_candidate):
            if escape_next:
                escape_next = False
                continue
            if char == "\\" and in_string:
                escape_next = True
                continue
            if char == '"' and (not escape_next):
                in_string = not in_string
                continue
            if not in_string:
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        try:
                            complete_json = json_candidate[: i + 1]
                            parsed = json.loads(complete_json)
                            if isinstance(parsed, dict) and "tool_call" in parsed:
                                return True
                        except json.JSONDecodeError:
                            pass
                        return False
        return False

    def _finish_streaming(self) -> None:
        """Finish streaming and clean up."""
        try:
            with self._processor_lock:
                self._queue_processor_running = False
                print("Queue processor flag reset in _finish_streaming")
            if hasattr(self, "processor") and self.processor:
                self.processor.stop()
                self.processor = None
            self.chat_display.set_server_mode(False)
            self.chat_display.config(state=tk.DISABLED)
            self.chat_state.finish_streaming()
            self.is_streaming = False
            self.update_submit_button_text()
            self.current_request_thread = None
            self._final_highlight()
            if hasattr(self, "_was_intelligent_wrap_active_before_submit"):
                self._was_intelligent_wrap_active_before_submit = False
                print("Cleared _was_intelligent_wrap_active_before_submit flag")
        except Exception as e:
            print(f"Error in _finish_streaming: {e}")
            import traceback

            traceback.print_exc()
            self.is_streaming = False
            with self._processor_lock:
                self._queue_processor_running = False

    def _process_streaming_response(
        self,
        payload: dict,
        answer_index: int,
        is_continuation: bool = False,
    ) -> None:
        """Modified version that filters out tool call JSON from display while still detecting complete tool calls."""
        response = None
        try:
            if not self.is_streaming:
                print(
                    f"WARNING: Attempted to process streaming response when not streaming (answer {answer_index})",
                )
                return
            request_type = "continuation" if is_continuation else "initial"
            print(
                f"Starting {request_type} API request for answer index {answer_index}",
            )
            accumulated_content = ""
            last_sent_position = 0
            tool_call_buffer = ""
            in_potential_tool_call = False
            response = requests.post(BASE_URL, json=payload, stream=True, timeout=300)
            if response.status_code != 200:
                error_msg = f"{request_type.capitalize()} API Error: Status code {response.status_code}"
                print(error_msg)
                error_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=error_msg,
                    is_done=True,
                    is_error=True,
                )
                self._put_content_update_with_retry(error_update)
                return
            try:
                for line in response.iter_lines(decode_unicode=True):
                    if self.stop_streaming_flag.is_set():
                        print(
                            f"Streaming stopped during {request_type} response for answer {answer_index}",
                        )
                        self._graceful_connection_close(response)
                        return
                    if not line:
                        continue
                    try:
                        data = json.loads(line.strip())
                        if "message" in data and "content" in data["message"]:
                            content_chunk = data["message"]["content"]
                            if content_chunk:
                                accumulated_content += content_chunk
                                if (
                                    '{"tool_call"' in content_chunk
                                    or '{ "tool_call"' in content_chunk
                                    or '{\n  "tool_call"' in content_chunk
                                ):
                                    in_potential_tool_call = True
                                    tool_call_buffer = ""
                                if in_potential_tool_call:
                                    tool_call_buffer += content_chunk
                                    if self._is_complete_json_object(tool_call_buffer):
                                        print(
                                            f"🔧 COMPLETE TOOL CALL DETECTED - Filtering from display and executing!",
                                        )
                                        indicator_update = ContentUpdate(
                                            answer_index=answer_index,
                                            content_chunk="\n\n⚡ **Tool call detected - executing immediately...**\n",
                                            is_done=False,
                                            is_error=False,
                                        )
                                        self._put_content_update_with_retry(
                                            indicator_update,
                                        )
                                        connection_id = (
                                            f"stream_{answer_index}_{int(time.time())}"
                                        )
                                        self._handle_tool_calls_with_managed_connection(
                                            accumulated_content,
                                            answer_index,
                                            response,
                                            connection_id,
                                        )
                                        return
                                    continue
                                else:
                                    chunk_to_send = content_chunk
                                    content_update = ContentUpdate(
                                        answer_index=answer_index,
                                        content_chunk=chunk_to_send,
                                        is_done=False,
                                        is_error=False,
                                    )
                                    self._put_content_update_with_retry(content_update)
                                    last_sent_position = len(accumulated_content)
                        if data.get("done", False):
                            if self.stop_streaming_flag.is_set():
                                print(
                                    f"Streaming stopped before {request_type} completion for answer {answer_index}",
                                )
                                self._graceful_connection_close(response)
                                return
                            print(
                                f"{request_type.capitalize()} stream completed normally for answer {answer_index}",
                            )
                            if (
                                self.is_streaming
                                and self._detect_complete_tool_call_in_stream(
                                    accumulated_content,
                                )
                            ):
                                print(
                                    f"Found tool calls at stream end (backup detection)",
                                )
                                connection_id = (
                                    f"stream_{answer_index}_{int(time.time())}"
                                )
                                self._handle_tool_calls_with_managed_connection(
                                    accumulated_content,
                                    answer_index,
                                    response,
                                    connection_id,
                                )
                                return
                            done_update = ContentUpdate(
                                answer_index=answer_index,
                                content_chunk="",
                                is_done=True,
                                is_error=False,
                            )
                            self._put_content_update_with_retry(done_update)
                            self._graceful_connection_close(response)
                            return
                    except json.JSONDecodeError as json_err:
                        print(
                            f"Skipping malformed JSON in {request_type} stream: {line[:100]}",
                        )
                        continue
                    except Exception as content_err:
                        print(
                            f"Error processing {request_type} content chunk: {content_err}",
                        )
                        continue
            except (
                BrokenPipeError,
                ConnectionResetError,
                requests.exceptions.ChunkedEncodingError,
            ) as conn_err:
                print(
                    f"Connection error during {request_type} stream: {type(conn_err).__name__}",
                )
                if (
                    self.is_streaming
                    and accumulated_content
                    and self._detect_complete_tool_call_in_stream(accumulated_content)
                ):
                    print(
                        f"Found tool calls in accumulated content after connection error",
                    )
                    connection_id = f"stream_{answer_index}_{int(time.time())}"
                    self._handle_tool_calls_with_managed_connection(
                        accumulated_content,
                        answer_index,
                        response,
                        connection_id,
                    )
                    return
                if not self.stop_streaming_flag.is_set():
                    error_update = ContentUpdate(
                        answer_index=answer_index,
                        content_chunk=f"Connection error: {type(conn_err).__name__}",
                        is_done=True,
                        is_error=True,
                    )
                    self._put_content_update_with_retry(error_update)
                return
            print(f"{request_type.capitalize()} stream ended without done flag")
            if (
                self.is_streaming
                and accumulated_content
                and self._detect_complete_tool_call_in_stream(accumulated_content)
            ):
                print(f"Found tool calls in accumulated content after stream end")
                connection_id = f"stream_{answer_index}_{int(time.time())}"
                self._handle_tool_calls_with_managed_connection(
                    accumulated_content,
                    answer_index,
                    response,
                    connection_id,
                )
                return
            if in_potential_tool_call and tool_call_buffer:
                content_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=tool_call_buffer,
                    is_done=False,
                    is_error=False,
                )
                self._put_content_update_with_retry(content_update)
            done_update = ContentUpdate(
                answer_index=answer_index,
                content_chunk="",
                is_done=True,
                is_error=False,
            )
            self._put_content_update_with_retry(done_update)
        except requests.exceptions.Timeout:
            error_msg = f"{request_type.capitalize()} request timed out"
            print(error_msg)
            if not self.stop_streaming_flag.is_set():
                error_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=error_msg,
                    is_done=True,
                    is_error=True,
                )
                self._put_content_update_with_retry(error_update)
        except requests.exceptions.ConnectionError:
            error_msg = (
                f"{request_type.capitalize()} connection error - is Ollama running?"
            )
            print(error_msg)
            if not self.stop_streaming_flag.is_set():
                error_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=error_msg,
                    is_done=True,
                    is_error=True,
                )
                self._put_content_update_with_retry(error_update)
        except Exception as e:
            error_msg = f"Unexpected error in {request_type}: {str(e)}"
            print(error_msg)
            import traceback

            traceback.print_exc()
            if not self.stop_streaming_flag.is_set():
                error_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=error_msg,
                    is_done=True,
                    is_error=True,
                )
                self._put_content_update_with_retry(error_update)
        finally:
            if response is not None and (not hasattr(self, "_connection_transferred")):
                self._graceful_connection_close(response)


ChatTabStreaming = ChatTabStreamingAdvanced
