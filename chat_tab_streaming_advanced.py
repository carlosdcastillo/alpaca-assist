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

    def _set_chat_display_streaming_mode(self, streaming: bool) -> None:
        """Set chat display to streaming mode (read-only) or normal mode (editable)."""
        if streaming:
            # Streaming mode: read-only
            self.chat_display.config(state=tk.DISABLED)
            print("Chat display set to streaming mode (read-only)")
        else:
            # Normal mode: editable
            self.chat_display.ensure_caret_enabled()
            print("Chat display set to normal mode (editable)")

    def _insert_streaming_content_safe(self, content: str, insert_pos: str) -> None:
        """Insert content during streaming without allowing user editing."""
        if not content:
            return

        # Temporarily enable for insertion
        current_state = self.chat_display.cget("state")
        self.chat_display.config(state=tk.NORMAL)

        # Insert content
        self.chat_display.insert(insert_pos, content)

        # Restore previous state (should be DISABLED during streaming)
        self.chat_display.config(state=current_state)

        # Scroll to show new content if we were at bottom
        if self._was_at_bottom():
            self._scroll_to_bottom_safely()

    def _scroll_to_bottom_safely(self) -> None:
        """Safely scroll to bottom with error handling."""
        try:
            current_xview = self.chat_display.xview()
            self.chat_display.yview_moveto(1.0)
            if current_xview:
                self.chat_display.xview_moveto(current_xview[0])
        except tk.TclError:
            self.chat_display.see(tk.END)

    def _prepare_messages_from_payload(
        self,
        data_payload: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Prepare messages from data payload."""
        if "messages" in data_payload:
            return data_payload["messages"]

        messages: list[dict[str, str]] = []
        for q, a in zip(
            data_payload["chat_history_questions"],
            data_payload["chat_history_answers"],
        ):
            if q.strip() and a.strip():
                expanded_q = expand(q)
                messages.append({"role": "user", "content": expanded_q})
                self._add_tool_messages(messages, a)

        messages.append({"role": "user", "content": data_payload["prompt"]})
        return messages

    def _add_tool_messages(
        self,
        messages: list[dict[str, str]],
        answer_content: str,
    ) -> None:
        """Add tool-related messages from answer content."""
        (
            assistant_content,
            tool_results,
            jsons,
        ) = self._extract_tool_results_from_content(answer_content)

        if assistant_content.strip():
            messages.append({"role": "assistant", "content": assistant_content})

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

    def _create_ollama_payload(
        self,
        data_payload: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Create the payload for Ollama API."""
        ollama_payload: dict[str, Any] = {
            "model": data_payload["model"],
            "messages": messages,
            "stream": True,
        }

        available_tools = self.parent.get_available_mcp_tools()
        if available_tools:
            ollama_payload["tools"] = available_tools

        return ollama_payload

    def _handle_api_error(self, e: Exception, answer_index: int) -> None:
        """Handle API errors and send error updates."""
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

            messages = self._prepare_messages_from_payload(data_payload)
            ollama_payload = self._create_ollama_payload(data_payload, messages)

            print(f"Starting API request for answer index {answer_index}")
            print(f"Tools included: {len(ollama_payload.get('tools', []))} tools")
            print(f"Messages: {len(messages)} total")

            self._process_streaming_response(
                ollama_payload,
                answer_index,
                is_continuation=False,
            )
        except Exception as e:
            self._handle_api_error(e, answer_index)
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

            if tool_call_positions:
                results = self._execute_tool_calls_sequentially(
                    tool_call_positions,
                    modified_text,
                    response,
                )
            return results
        finally:
            with self._tool_execution_lock:
                self._pending_tool_executions = max(
                    0,
                    self._pending_tool_executions - 1,
                )

    def _execute_tool_calls_sequentially(
        self,
        tool_call_positions: list,
        modified_text: str,
        response: requests.Response | None,
    ) -> list[str]:
        """Execute tool calls one by one and collect results."""
        results = []
        results_dict = {}

        try:
            for i, (start_pos, end_pos) in enumerate(tool_call_positions):
                try:
                    idx, result = self._execute_single_tool_with_connection(
                        i,
                        start_pos,
                        end_pos,
                        modified_text,
                        response,
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

    def _execute_single_tool_with_connection(
        self,
        i: int,
        start_pos: int,
        end_pos: int,
        modified_text: str,
        response: requests.Response | None,
    ) -> tuple[int, str]:
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

    def _parse_tool_call_json(
        self,
        text: str,
        start_pos: int,
        end_pos: int,
    ) -> dict | None:
        """Parse tool call JSON with multiple fallback attempts."""
        json_str = text[start_pos:end_pos]
        json_str_clean = WHITESPACE_PATTERN.sub(" ", json_str)
        json_variants = [json_str, json_str_clean]

        if json_str_clean.endswith("}") and json_str_clean.count(
            "{",
        ) > json_str_clean.count("}"):
            json_variants.append(json_str_clean + "}")
            print("Added missing closing brace to tool call JSON for execution")

        for variant in json_variants:
            try:
                return json.loads(variant)
            except json.JSONDecodeError:
                continue

        return None

    def _create_progress_manager(
        self,
        tool_name: str,
        managed_response: requests.Response | None,
    ):
        """Create appropriate progress manager based on available imports."""
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
            return progress_manager
        except ImportError:
            from tool_progress_manager import ToolProgressManager

            progress_manager = ToolProgressManager(tool_name, self)
            progress_manager.start()
            return progress_manager

    def _execute_mcp_tool(
        self,
        server_name: str,
        actual_tool_name: str,
        arguments: dict,
    ) -> str:
        """Execute MCP tool and return result."""
        mcp_manager = getattr(self.parent, "mcp_manager", None)
        main_loop = getattr(self.parent, "event_loop", None)

        if not (mcp_manager and main_loop):
            return "MCP Manager not available"

        import asyncio

        future = asyncio.run_coroutine_threadsafe(
            mcp_manager.call_tool(server_name, actual_tool_name, arguments),
            main_loop,
        )

        return self._wait_for_tool_result(future)

    def _wait_for_tool_result(self, future) -> str:
        """Wait for tool execution result with progress updates."""
        max_wait = 300
        check_interval = 0.1
        elapsed = 0.0

        # Quick check first
        try:
            result = future.result(timeout=0.1)
            return (
                json.dumps(result, indent=2) if result else "Tool execution completed"
            )
        except TimeoutError:
            pass

        # Wait with progress updates
        while elapsed < max_wait:
            if future.done():
                try:
                    result = future.result(timeout=0.01)
                    return (
                        json.dumps(result, indent=2)
                        if result
                        else "Tool execution completed"
                    )
                except Exception as e:
                    return f"Error executing tool: {str(e)}"

            time.sleep(check_interval)
            elapsed += check_interval

        future.cancel()
        return f"Tool execution timed out after {max_wait} seconds"

    def _execute_tool_call_with_connection_management(
        self,
        text: str,
        start_pos: int,
        end_pos: int,
        managed_response: requests.Response | None = None,
    ) -> str | None:
        """Execute a specific tool call with enhanced connection management."""
        try:
            tool_call_data = self._parse_tool_call_json(text, start_pos, end_pos)
            if tool_call_data is None:
                print(f"Failed to parse tool call JSON after cleaning attempts")
                return f"Error parsing tool call JSON: Could not parse after cleaning"

            if "tool_call" not in tool_call_data:
                return "Error: Invalid tool call format"

            tool_call = tool_call_data["tool_call"]
            tool_name = tool_call["name"]
            arguments = tool_call.get("arguments", {})

            print(f"Executing tool: {tool_name} with args: {arguments}")

            progress_manager = self._create_progress_manager(
                tool_name,
                managed_response,
            )

            try:
                if "_" not in tool_name:
                    progress_manager.error("Invalid tool name format")
                    return f"Error: Invalid tool name format: {tool_name}"

                server_name, actual_tool_name = tool_name.split("_", 1)
                print(f"Server name: {server_name}, Tool name: {actual_tool_name}")

                result = self._execute_mcp_tool(
                    server_name,
                    actual_tool_name,
                    arguments,
                )
                progress_manager.complete()
                return result

            except Exception as e:
                progress_manager.error(str(e))
                return f"Error executing tool: {str(e)}"
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

    def _clear_continuation_states(self) -> None:
        """Clear continuation states on stop."""
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

    def _clear_pending_tool_executions(self) -> None:
        """Clear pending tool executions."""
        with self._tool_execution_lock:
            if self._pending_tool_executions > 0:
                print(
                    f"Clearing {self._pending_tool_executions} pending tool executions",
                )
                self._pending_tool_executions = 0

    def _clear_content_queue(self) -> None:
        """Clear the content update queue."""
        while not self.content_update_queue.empty():
            try:
                self.content_update_queue.get_nowait()
            except queue.Empty:
                break

    def _add_stop_message_if_streaming(self) -> None:
        """Add stop message if currently streaming."""
        if self.chat_state.is_streaming():
            current_answer_index = len(self.chat_state.answers) - 1
            if current_answer_index >= 0:
                stop_message = "\n\n[Streaming stopped by user]"
                self.chat_state.append_to_answer(current_answer_index, stop_message)
                # Use safe insertion during streaming stop
                self._insert_streaming_content_safe(
                    stop_message,
                    self.answer_end_positions.get(current_answer_index, tk.END),
                )

    def stop_streaming(self) -> None:
        """Stop the current streaming request."""
        print("Stopping streaming...")
        self.stop_streaming_flag.set()
        self._stop_processor()

        if self.current_request_thread and self.current_request_thread.is_alive():
            print(
                f"Attempting to stop current request thread: {self.current_request_thread}",
            )

        self._clear_continuation_states()
        self._clear_pending_tool_executions()
        self._clear_content_queue()
        self._add_stop_message_if_streaming()

        self.chat_state.finish_streaming()
        self.is_streaming = False
        print(f"Force reset is_streaming to False")
        self.update_submit_button_text()
        self.current_request_thread = None

        # Re-enable editing after stopping
        self._set_chat_display_streaming_mode(False)
        self.parent.master.after(100, self._final_highlight)

    def _post_process_content(
        self,
        accumulated_content: str,
        answer_index: int,
    ) -> None:
        """Post-process accumulated content to check for OpenAI format tool calls."""
        try:
            if not self._should_process_content(answer_index):
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

    def _should_process_content(self, answer_index: int) -> bool:
        """Check if content should be processed."""
        if not self.is_streaming:
            print(
                f"Skipping post-process - not actively streaming (answer {answer_index})",
            )
            return False
        if self.stop_streaming_flag.is_set():
            print(f"Skipping post-process - stop flag is set (answer {answer_index})")
            return False
        return True

    def _prepare_continuation_messages(
        self,
        questions: list,
        answers: list,
        answer_index: int,
    ) -> list[dict[str, str]]:
        """Prepare messages for continuation request."""
        messages: list[dict[str, str]] = []

        for i, (q, a) in enumerate(zip(questions, answers)):
            a = a.get_text_content()
            if i <= answer_index and q.strip():
                messages.append({"role": "user", "content": expand(q)})
                if a.strip():
                    self._add_tool_messages_for_continuation(messages, a)
            elif i > answer_index:
                break

        return messages

    def _add_tool_messages_for_continuation(
        self,
        messages: list[dict[str, str]],
        answer_content: str,
    ) -> None:
        """Add tool messages for continuation request."""
        (
            assistant_content,
            tool_results,
            jsons,
        ) = self._extract_tool_results_from_content(answer_content)

        if assistant_content.strip():
            messages.append({"role": "assistant", "content": assistant_content})

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

    def _create_continuation_payload(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Create payload for continuation request."""
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

        return continuation_payload

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

            messages = self._prepare_continuation_messages(
                questions,
                answers,
                answer_index,
            )
            continuation_payload = self._create_continuation_payload(messages)

            print(f"Prepared {len(messages)} messages for continuation")

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
            self._handle_continuation_error(e, answer_index)

    def _handle_continuation_error(self, e: Exception, answer_index: int) -> None:
        """Handle errors in continuation request."""
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

    def _find_answer_boundaries(
        self,
        lines: list[str],
        answer_index: int,
    ) -> tuple[int | None, int | None]:
        """Find start and end line boundaries for a specific answer."""
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

        if answer_end_line is None:
            answer_end_line = len(lines)

        return answer_start_line, answer_end_line

    def _replace_openai_format_in_text(self, answer_text: str) -> tuple[str, bool]:
        """Replace OpenAI format with internal format in text."""
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
        return new_answer_text, modified

    def _update_display_with_new_text(
        self,
        answer_start_line: int,
        answer_end_line: int,
        new_answer_text: str,
        lines: list[str],
    ) -> None:
        """Update the display with new text."""
        start_pos = f"{answer_start_line + 1}.0"
        end_pos = f"{answer_end_line + 1}.0"
        self.chat_display.delete(start_pos, end_pos)
        self.chat_display.insert(
            start_pos,
            new_answer_text + "\n" if answer_end_line < len(lines) else new_answer_text,
        )
        print(f"Replaced OpenAI tool call format with internal format in answer")
        self.chat_display.highlight_text()

    def _replace_openai_tool_calls_in_display(self, answer_index: int) -> None:
        """Thread-safe version that ensures UI operations run on main thread."""
        if threading.current_thread() != threading.main_thread():
            self.parent.master.after_idle(
                lambda: self._replace_openai_tool_calls_in_display(answer_index),
            )
            return

        try:
            # Temporarily enable for editing
            current_state = self.chat_display.cget("state")
            self.chat_display.config(state=tk.NORMAL)

            full_content = self.chat_display.get("1.0", tk.END)
            lines = full_content.split("\n")

            answer_start_line, answer_end_line = self._find_answer_boundaries(
                lines,
                answer_index,
            )

            if answer_start_line is None:
                print(f"Could not find answer {answer_index} in display")
                return

            answer_lines = lines[answer_start_line:answer_end_line]
            answer_text = "\n".join(answer_lines)

            new_answer_text, modified = self._replace_openai_format_in_text(answer_text)

            if modified:
                self._update_display_with_new_text(
                    answer_start_line,
                    answer_end_line,
                    new_answer_text,
                    lines,
                )

            # Restore previous state
            self.chat_display.config(state=current_state)
        except Exception as e:
            print(f"Error replacing OpenAI tool calls in display: {e}")
            import traceback

            traceback.print_exc()
            # Ensure we restore the state even on error
            try:
                self.chat_display.config(state=current_state)
            except:
                self.chat_display.config(state=tk.DISABLED)

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

        # Extract tool results
        for match in TOOL_RESULT_PATTERN.finditer(content):
            tool_result = match.group(1).strip()
            if tool_result:
                tool_results.append(tool_result)

        clean_content = TOOL_RESULT_PATTERN.sub("", clean_content)

        # Extract internal format tool calls
        tool_call_jsons.extend(self._extract_internal_tool_calls(content))

        # Extract OpenAI format tool calls
        tool_call_jsons.extend(self._extract_openai_tool_calls(content))

        clean_content = self._filter_tool_calls_from_content(clean_content)
        clean_content = MULTIPLE_NEWLINES_PATTERN.sub("\n\n", clean_content).strip()

        return (clean_content, tool_results, tool_call_jsons)

    def _extract_internal_tool_calls(self, content: str) -> list[dict]:
        """Extract internal format tool calls from content."""
        tool_call_jsons = []
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

        return tool_call_jsons

    def _extract_openai_tool_calls(self, content: str) -> list[dict]:
        """Extract OpenAI format tool calls from content."""
        tool_call_jsons = []
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

        return tool_call_jsons

    def _insert_content_at_answer(self, answer_index: int, content: str) -> None:
        """Thread-safe version that ensures UI operations run on main thread."""
        if not content:
            return

        if threading.current_thread() != threading.main_thread():
            self.parent.master.after_idle(
                lambda: self._insert_content_at_answer(answer_index, content),
            )
            return

        # Use safe insertion method during streaming
        if self.is_streaming:
            self._insert_content_streaming_mode(answer_index, content)
        else:
            self._insert_content_normal_mode(answer_index, content)

    def _insert_content_streaming_mode(self, answer_index: int, content: str) -> None:
        """Insert content in streaming mode."""
        if answer_index in self.answer_end_positions:
            insert_pos = self.answer_end_positions[answer_index]
        else:
            insert_pos = self._find_answer_position(answer_index)

        self._insert_streaming_content_safe(content, insert_pos)
        self.answer_end_positions[answer_index] = f"{insert_pos} + {len(content)}c"

    def _insert_content_normal_mode(self, answer_index: int, content: str) -> None:
        """Insert content in normal (non-streaming) mode."""
        self.chat_display.set_server_mode(True)
        self.chat_display.config(state=tk.NORMAL)

        if answer_index in self.answer_end_positions:
            insert_pos = self.answer_end_positions[answer_index]
        else:
            insert_pos = self._find_answer_position(answer_index)

        self.chat_display.insert(insert_pos, content)
        self.answer_end_positions[answer_index] = f"{insert_pos} + {len(content)}c"

        if self._was_at_bottom():
            self._scroll_to_bottom_safely()

        self.chat_display.set_server_mode(False)

    def _find_tool_call_start_positions(self, text: str) -> list[int]:
        """Find all possible tool call start positions in text."""
        positions = []
        for pattern in ['{"tool_call"', '{ "tool_call"', '{\n  "tool_call"']:
            start_idx = text.find(pattern)
            if start_idx != -1:
                positions.append(start_idx)
        return positions

    def _parse_json_at_position(
        self,
        text: str,
        start_idx: int,
    ) -> tuple[bool, dict | None]:
        """Parse JSON starting at given position and return success status and parsed data."""
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
                                return True, parsed
                        except json.JSONDecodeError:
                            pass
                        return False, None
        return False, None

    def _is_complete_json_object(self, text: str) -> bool:
        """
        Check if the text contains a complete JSON object for a tool call.
        Returns True if the JSON is complete and valid.
        """
        if not text or '{"tool_call"' not in text:
            return False

        start_positions = self._find_tool_call_start_positions(text)

        for start_idx in start_positions:
            is_complete, _ = self._parse_json_at_position(text, start_idx)
            if is_complete:
                return True

        return False

    def _handle_api_response_error(
        self,
        response: requests.Response,
        request_type: str,
        answer_index: int,
    ) -> bool:
        """Handle API response errors. Returns True if error was handled."""
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
            return True
        return False

    def _process_content_chunk(
        self,
        content_chunk: str,
        accumulated_content: str,
        in_potential_tool_call: bool,
        tool_call_buffer: str,
        answer_index: int,
        response: requests.Response,
    ) -> tuple[str, bool, str, bool]:
        """Process a single content chunk and return updated state."""
        accumulated_content += content_chunk

        # Check for tool call start
        if any(
            pattern in content_chunk
            for pattern in ['{"tool_call"', '{ "tool_call"', '{\n  "tool_call"']
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
                self._put_content_update_with_retry(indicator_update)
                connection_id = f"stream_{answer_index}_{int(time.time())}"
                self._handle_tool_calls_with_managed_connection(
                    accumulated_content,
                    answer_index,
                    response,
                    connection_id,
                )
                return (
                    accumulated_content,
                    in_potential_tool_call,
                    tool_call_buffer,
                    True,
                )
        else:
            chunk_to_send = content_chunk
            content_update = ContentUpdate(
                answer_index=answer_index,
                content_chunk=chunk_to_send,
                is_done=False,
                is_error=False,
            )
            self._put_content_update_with_retry(content_update)

        return accumulated_content, in_potential_tool_call, tool_call_buffer, False

    def _handle_stream_completion(
        self,
        accumulated_content: str,
        answer_index: int,
        response: requests.Response,
        request_type: str,
    ) -> bool:
        """Handle stream completion. Returns True if handled (should return from caller)."""
        if self.stop_streaming_flag.is_set():
            print(
                f"Streaming stopped before {request_type} completion for answer {answer_index}",
            )
            self._graceful_connection_close(response)
            return True

        print(
            f"{request_type.capitalize()} stream completed normally for answer {answer_index}",
        )

        if self.is_streaming and self._detect_complete_tool_call_in_stream(
            accumulated_content,
        ):
            print(f"Found tool calls at stream end (backup detection)")
            connection_id = f"stream_{answer_index}_{int(time.time())}"
            self._handle_tool_calls_with_managed_connection(
                accumulated_content,
                answer_index,
                response,
                connection_id,
            )
            return True

        done_update = ContentUpdate(
            answer_index=answer_index,
            content_chunk="",
            is_done=True,
            is_error=False,
        )
        self._put_content_update_with_retry(done_update)
        self._graceful_connection_close(response)
        return True

    def _handle_connection_error(
        self,
        conn_err: Exception,
        accumulated_content: str,
        answer_index: int,
        response: requests.Response,
        request_type: str,
    ) -> None:
        """Handle connection errors during streaming."""
        print(
            f"Connection error during {request_type} stream: {type(conn_err).__name__}",
        )

        if (
            self.is_streaming
            and accumulated_content
            and self._detect_complete_tool_call_in_stream(accumulated_content)
        ):
            print(f"Found tool calls in accumulated content after connection error")
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

    def _handle_stream_end_without_done(
        self,
        accumulated_content: str,
        answer_index: int,
        response: requests.Response,
        in_potential_tool_call: bool,
        tool_call_buffer: str,
        request_type: str,
    ) -> None:
        """Handle stream end without done flag."""
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
            tool_call_buffer = ""
            in_potential_tool_call = False

            response = requests.post(BASE_URL, json=payload, stream=True, timeout=300)

            if self._handle_api_response_error(response, request_type, answer_index):
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
                                (
                                    accumulated_content,
                                    in_potential_tool_call,
                                    tool_call_buffer,
                                    should_return,
                                ) = self._process_content_chunk(
                                    content_chunk,
                                    accumulated_content,
                                    in_potential_tool_call,
                                    tool_call_buffer,
                                    answer_index,
                                    response,
                                )
                                if should_return:
                                    return

                        if data.get("done", False):
                            if self._handle_stream_completion(
                                accumulated_content,
                                answer_index,
                                response,
                                request_type,
                            ):
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
                self._handle_connection_error(
                    conn_err,
                    accumulated_content,
                    answer_index,
                    response,
                    request_type,
                )
                return

            self._handle_stream_end_without_done(
                accumulated_content,
                answer_index,
                response,
                in_potential_tool_call,
                tool_call_buffer,
                request_type,
            )

        except requests.exceptions.Timeout:
            self._handle_timeout_error(answer_index, request_type)
        except requests.exceptions.ConnectionError:
            self._handle_connection_error_exception(answer_index, request_type)
        except Exception as e:
            self._handle_unexpected_error(e, answer_index, request_type)
        finally:
            if response is not None and (not hasattr(self, "_connection_transferred")):
                self._graceful_connection_close(response)

    def _handle_timeout_error(self, answer_index: int, request_type: str) -> None:
        """Handle timeout errors."""
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

    def _handle_connection_error_exception(
        self,
        answer_index: int,
        request_type: str,
    ) -> None:
        """Handle connection error exceptions."""
        error_msg = f"{request_type.capitalize()} connection error - is Ollama running?"
        print(error_msg)
        if not self.stop_streaming_flag.is_set():
            error_update = ContentUpdate(
                answer_index=answer_index,
                content_chunk=error_msg,
                is_done=True,
                is_error=True,
            )
            self._put_content_update_with_retry(error_update)

    def _handle_unexpected_error(
        self,
        e: Exception,
        answer_index: int,
        request_type: str,
    ) -> None:
        """Handle unexpected errors."""
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

    def _register_connection_if_available(
        self,
        response: requests.Response,
        connection_id: str,
    ) -> None:
        """Register connection with manager if available."""
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
                print(f"Registered connection {connection_id} for management")
            except ImportError:
                print(
                    "Enhanced connection manager not available, using standard approach",
                )

    def _process_tool_calls_for_chat_state(
        self,
        accumulated_content: str,
        answer_index: int,
    ) -> list[str]:
        """Process tool calls and add them to chat state."""
        tool_positions, _ = self._find_tool_calls(accumulated_content)
        tool_call_ids = []

        for i, (start_pos, end_pos) in enumerate(tool_positions):
            try:
                json_str = accumulated_content[start_pos:end_pos]
                json_str_clean = re.sub("\\s+", " ", json_str)
                tool_call_data = json.loads(json_str_clean)
                if "tool_call" in tool_call_data:
                    tool_call = tool_call_data["tool_call"]
                    tool_id = tool_call.get("id", f"tool_{i}_{int(time.time())}")
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

        return tool_call_ids

    def _process_tool_results(
        self,
        tool_results: list[str],
        tool_call_ids: list[str],
        answer_index: int,
    ) -> None:
        """Process and display tool results."""
        for i, tool_result in enumerate(tool_results):
            if tool_result:
                tool_id = (
                    tool_call_ids[i] if i < len(tool_call_ids) else f"tool_{i}_result"
                )

                try:
                    data_result = json.loads(tool_result)
                    if isinstance(data_result, dict) and "content" in data_result:
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

                value = self._format_json_for_display(value)
                self.chat_state.add_tool_result_to_answer(answer_index, value, tool_id)
                print(f"Added ToolResult with ID {tool_id} to chat state")

                result_text = f"\n\n**Tool {i + 1} Result:**\n```\n{value}\n```"
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

    def _cleanup_connection_management(
        self,
        response: requests.Response | None,
        connection_id: str,
    ) -> None:
        """Clean up connection management resources."""
        if response and self.connection_manager:
            try:
                self.connection_manager.release_connection(connection_id)
                print(f"Released connection {connection_id} from management")
            except:
                pass

        if response:
            self._graceful_connection_close(response)

    def _start_continuation_after_tools(
        self,
        answer_index: int,
        tool_results: list[str],
    ) -> None:
        """Start continuation after tool execution."""
        continuation_thread = threading.Thread(
            target=self._continue_after_tool_calls,
            args=(answer_index, tool_results),
            daemon=True,
        )
        time.sleep(1)
        continuation_thread.start()

    def _handle_tool_execution_error(
        self,
        tool_error: Exception,
        response: requests.Response | None,
        connection_id: str,
        answer_index: int,
    ) -> None:
        """Handle tool execution errors."""
        print(f"Error executing tool calls: {tool_error}")
        import traceback

        traceback.print_exc()

        self._cleanup_connection_management(response, connection_id)

        error_text = f"\n\n**Tool Execution Error:** {str(tool_error)}"
        error_update = ContentUpdate(
            answer_index=answer_index,
            content_chunk=error_text,
            is_done=True,
            is_error=True,
        )
        self._put_content_update_with_retry(error_update)

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

                    self._register_connection_if_available(response, connection_id)

                    tool_call_ids = self._process_tool_calls_for_chat_state(
                        accumulated_content,
                        answer_index,
                    )

                    tool_results = self._execute_all_tool_calls_with_connection(
                        accumulated_content,
                        response,
                    )

                    if self.stop_streaming_flag.is_set():
                        print("Stopping tool execution - stop flag set")
                        return

                    self._process_tool_results(
                        tool_results,
                        tool_call_ids,
                        answer_index,
                    )

                    print(
                        f"Tool execution completed for answer {answer_index}, starting continuation...",
                    )

                    self._cleanup_connection_management(response, connection_id)
                    self._start_continuation_after_tools(answer_index, tool_results)

                except Exception as tool_error:
                    self._handle_tool_execution_error(
                        tool_error,
                        response,
                        connection_id,
                        answer_index,
                    )

            tool_thread = threading.Thread(target=execute_and_continue, daemon=True)
            tool_thread.start()
        except Exception as e:
            print(f"Error in connection-managed tool execution: {e}")
            if response:
                self._graceful_connection_close(response)

    def _format_json_for_display(self, content: str) -> str:
        """Format JSON content for display, handling both raw JSON and code blocks."""
        if not content:
            return content

        if content.startswith("```") and content.endswith("```"):
            return self._format_code_block_json(content)
        else:
            return self._format_raw_json(content)

    def _format_code_block_json(self, content: str) -> str:
        """Format JSON content within code blocks."""
        lines = content.split("\n")
        if len(lines) >= 3:
            inner_content = "\n".join(lines[1:-1])
            try:
                parsed = json.loads(inner_content)
                formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
                return f"```json\n{formatted}\n```"
            except (json.JSONDecodeError, TypeError):
                return content
        return content

    def _format_raw_json(self, content: str) -> str:
        """Format raw JSON content."""
        try:
            parsed = json.loads(content)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return content

    def _expand_prompt_if_available(self, expanded_input: str) -> str:
        """Expand prompts if prompt manager is available."""
        if hasattr(self.parent, "prompt_manager"):

            def expand_prompt(match):
                trigger = match.group(1)
                prompt = self.parent.prompt_manager.get_prompt_by_trigger(trigger)
                return prompt.body if prompt else match.group(0)

            return PROMPT_PATTERN.sub(expand_prompt, expanded_input)
        return expanded_input

    def _setup_chat_display_for_answer(
        self,
        answer_index: int,
        expanded_input: str,
    ) -> None:
        """Set up chat display for new answer."""
        if answer_index > 0:
            separator = "-" * 80
            self._insert_structural_content(f"\n\n{separator}\n\n")

        self._insert_structural_content(f"Q: {expanded_input}\n\n")
        self._insert_structural_content(f"A:\n\n")
        self.answer_end_positions[answer_index] = self.chat_display.index(
            tk.END + " -1c",
        )

    def _create_data_payload(
        self,
        expanded_input: str,
        answer_index: int,
    ) -> dict[str, Any]:
        """Create data payload for API request."""
        selected_model = self.parent.get_selected_model()
        questions, answers, _ = self.chat_state.get_safe_copy_full()

        return {
            "prompt": expanded_input,
            "model": selected_model,
            "chat_history_questions": questions,
            "chat_history_answers": [x.get_text_content() for x in answers],
            "answer_index": answer_index,
        }

    def _start_streaming_request(self, answer_index: int) -> None:
        """Start the streaming request thread."""
        self.is_streaming = True
        self.stop_streaming_flag.clear()

        # Set chat display to streaming mode (read-only)
        self._set_chat_display_streaming_mode(True)

        self.update_submit_button_text()
        self._start_processor_if_needed()
        self.current_request_thread = threading.Thread(
            target=self.fetch_api_response,
            args=(answer_index,),
            daemon=True,
        )
        self.current_request_thread.start()

    def submit_message(self) -> str:
        """Submit the message to the API and handle the response."""
        if self.is_streaming:
            self.stop_streaming()
            return "break"

        user_input = self.input_field.get("1.0", tk.END).strip()
        if not user_input:
            return "break"

        expanded_input = expand(user_input)
        expanded_input = self._expand_prompt_if_available(expanded_input)

        self.hide_autocomplete_menu()
        answer_index = self.chat_state.add_question(expanded_input)
        self.input_field.delete("1.0", tk.END)

        self._setup_chat_display_for_answer(answer_index, expanded_input)

        data_payload = self._create_data_payload(expanded_input, answer_index)
        self.input_queue.put(data_payload)

        self._start_streaming_request(answer_index)

        return "break"

    def _stop_processor(self):
        """Atomically stop the queue processor with proper synchronization."""
        with self._processor_lock:
            was_running = self._queue_processor_running
            self._queue_processor_running = False
            if was_running:
                print("Queue processor stopped")
            return was_running

    def _process_single_update(
        self,
        update: ContentUpdate,
        chars_since_newline: int,
        newline_threshold: int,
    ) -> tuple[str, int, bool]:
        """Process a single content update and return content, chars count, and streaming status."""
        streaming_finished = False

        if update.is_error:
            error_content = f"\n\n[Error: {update.content_chunk}]"
            self.chat_state.append_to_answer(update.answer_index, error_content)
            self.chat_state.finish_streaming()
            content_to_insert = error_content
            streaming_finished = True
            chars_since_newline = 0
        else:
            original_content = update.content_chunk
            content_to_insert = self._add_newlines_to_long_content(
                original_content,
                chars_since_newline,
                newline_threshold,
            )

            if "\n" in content_to_insert:
                last_newline_pos = content_to_insert.rfind("\n")
                chars_since_newline = len(content_to_insert) - last_newline_pos - 1
            else:
                chars_since_newline += len(content_to_insert)

            self.chat_state.append_to_answer(update.answer_index, content_to_insert)

            if update.is_done:
                self.chat_state.finish_streaming()
                streaming_finished = True

        return content_to_insert, chars_since_newline, streaming_finished

    def _should_highlight_now(
        self,
        current_time: float,
        last_highlight_time: float,
        content_accumulated: int,
        updates_processed: int,
        content_to_insert: str,
        update: ContentUpdate,
        highlight_intervals: dict,
    ) -> bool:
        """Determine if highlighting should be performed now."""
        time_since_last_highlight = current_time - last_highlight_time
        is_tool_indicator = "⚡" in content_to_insert or "🔧" in content_to_insert
        is_code_block = "```" in content_to_insert

        return (
            time_since_last_highlight >= highlight_intervals["min_interval"]
            or content_accumulated >= highlight_intervals["content_threshold"]
            or updates_processed >= highlight_intervals["update_threshold"]
            or update.is_done
            or is_tool_indicator
            or is_code_block
        )

    def _process_queue_updates(
        self,
        cycle_limits: dict,
        highlight_intervals: dict,
    ) -> dict:
        """Process updates from the queue and return processing statistics."""
        updates_processed = 0
        streaming_finished = False
        content_accumulated = 0
        last_highlight_time = time.time()
        has_pending_updates = False
        chars_since_newline = getattr(self, "_chars_since_last_newline", 0)

        cycle_start_time = time.time()
        updates_this_cycle = 0

        while True:
            if updates_this_cycle >= cycle_limits["max_updates"]:
                print(
                    f"Reached update limit ({cycle_limits['max_updates']}), yielding control",
                )
                break
            if time.time() - cycle_start_time > cycle_limits["time_limit"]:
                print(
                    f"Cycle time limit reached ({cycle_limits['time_limit']}s), yielding control",
                )
                break

            try:
                update = self.content_update_queue.get(timeout=0.001)
                has_pending_updates = True
                updates_this_cycle += 1

                (
                    content_to_insert,
                    chars_since_newline,
                    update_streaming_finished,
                ) = self._process_single_update(
                    update,
                    chars_since_newline,
                    cycle_limits["newline_threshold"],
                )

                if update_streaming_finished:
                    streaming_finished = True

                self._chars_since_last_newline = chars_since_newline
                self._insert_content_at_answer(update.answer_index, content_to_insert)

                updates_processed += 1
                content_accumulated += len(content_to_insert)
                current_time = time.time()

                if self._should_highlight_now(
                    current_time,
                    last_highlight_time,
                    content_accumulated,
                    updates_processed,
                    content_to_insert,
                    update,
                    highlight_intervals,
                ):
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

        return {
            "updates_processed": updates_processed,
            "content_accumulated": content_accumulated,
            "streaming_finished": streaming_finished,
            "has_pending_updates": has_pending_updates,
            "updates_this_cycle": updates_this_cycle,
        }

    def _schedule_final_highlighting(self, updates_processed: int) -> None:
        """Schedule final highlighting after processing updates."""
        if updates_processed > 0:
            try:
                self.parent.master.after(10, lambda: self.chat_display.highlight_text())
                print(
                    f"Scheduled final highlighting after processing {updates_processed} updates",
                )
            except Exception as e:
                print(f"Error scheduling final highlight: {e}")

    def _handle_streaming_completion(self, streaming_finished: bool) -> bool:
        """Handle streaming completion. Returns True if processing should stop."""
        if streaming_finished and (not self._has_pending_tool_execution()):
            print("Streaming finished and no pending tool executions - finishing")
            try:
                self._finish_streaming()
                return True
            except Exception as e:
                print(f"Error in _finish_streaming: {e}")
                with self._processor_lock:
                    self._queue_processor_running = False
                return True
        return False

    def _schedule_next_processing_cycle(self, stats: dict) -> None:
        """Schedule the next processing cycle."""
        with self._processor_lock:
            if self._queue_processor_running:
                try:
                    if (
                        stats["has_pending_updates"]
                        and stats["updates_this_cycle"] >= 10
                    ):  # MAX_UPDATES_PER_CYCLE
                        delay = 10
                    elif (
                        stats["has_pending_updates"]
                        or self._has_pending_tool_execution()
                    ):
                        delay = 50
                    else:
                        delay = 200

                    self.parent.master.after(delay, self.process_content_queue)
                    print(f"Rescheduled queue processor with {delay}ms delay")
                except Exception as e:
                    print(f"Error rescheduling queue processor: {e}")
                    self._queue_processor_running = False
            else:
                print("Queue processor stopping - manually stopped")

    def process_content_queue(self) -> None:
        """Process queue with smart highlighting throttling and proper termination."""
        with self._processor_lock:
            if not self._queue_processor_running:
                print("Queue processor stopping - flag is False")
                return

        # Configuration constants
        cycle_limits = {
            "newline_threshold": 900000,
            "max_updates": 10,
            "time_limit": 0.05,
        }

        highlight_intervals = {
            "min_interval": 0.2,
            "content_threshold": 100,
            "update_threshold": 10,
        }

        try:
            stats = self._process_queue_updates(cycle_limits, highlight_intervals)
        except Exception as e:
            print(f"Error in queue processor: {e}")
            import traceback

            traceback.print_exc()
            stats = {
                "streaming_finished": True,
                "updates_processed": 0,
                "has_pending_updates": False,
                "updates_this_cycle": 0,
            }
        finally:
            self._schedule_final_highlighting(stats["updates_processed"])

            if self._handle_streaming_completion(stats["streaming_finished"]):
                return

            self._schedule_next_processing_cycle(stats)

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

            # Enable editing now that streaming is complete
            self._set_chat_display_streaming_mode(False)

            self.chat_state.finish_streaming()
            self.is_streaming = False
            self.update_submit_button_text()
            self.current_request_thread = None

            # Apply markdown formatting to the complete content
            if hasattr(self.chat_display, "finalize_rendering"):
                self.chat_display.finalize_rendering()

            self._final_highlight()
        except Exception as e:
            print(f"Error in _finish_streaming: {e}")
            import traceback

            traceback.print_exc()
            self.is_streaming = False
            with self._processor_lock:
                self._queue_processor_running = False


ChatTabStreaming = ChatTabStreamingAdvanced
