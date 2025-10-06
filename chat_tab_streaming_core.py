import concurrent.futures
import json
import queue
import re
import threading
import time
import tkinter as tk
from enum import Enum
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import requests

from expansion_language import expand
from tool_progress_manager import ToolProgressManager
from utils import ContentUpdate

BASE_URL: str = "http://localhost:11434/api/chat"


class ChatTabStreamingCore:
    """Streaming functionality for ChatTab - Core streaming, queue processing, and basic tool handling."""

    def _has_pending_tool_execution(self) -> bool:
        """Check if there are pending tool executions."""
        with self._tool_execution_lock:
            return self._pending_tool_executions > 0

    def _put_content_update_with_retry(
        self,
        update: ContentUpdate,
        max_retries: int = 3,
    ) -> bool:
        """Put content update with retry logic."""
        for attempt in range(max_retries):
            try:
                self.content_update_queue.put(update, timeout=1)
                return True
            except queue.Full:
                if attempt < max_retries - 1:
                    print(f"Queue full, retrying in {0.1 * (attempt + 1)}s...")
                    time.sleep(0.1 * (attempt + 1))
                else:
                    print(f"Failed to queue update after {max_retries} attempts")
                    return False
        return False

    def _stop_processor(self):
        """Atomically stop the queue processor."""
        with self._processor_lock:
            if self._queue_processor_running:
                print("Queue processor stopped")
                self._queue_processor_running = False

    def _start_processor_if_needed(self):
        """Start processor with proper synchronization."""
        with self._processor_lock:
            if not self._queue_processor_running:
                self._queue_processor_running = True
                print("Starting queue processor")
                self.parent.master.after_idle(self.process_content_queue)
                return True
            else:
                print("Queue processor already running")
        return False

    def _contains_tool_call(self, text: str) -> bool:
        """Check if text contains any complete tool calls."""
        positions, _ = self._find_tool_calls(text)
        return len(positions) > 0

    def _execute_all_tool_calls(self, text: str) -> list[str]:
        """Execute all tool calls found in the text and return their results."""
        with self._tool_execution_lock:
            self._pending_tool_executions += 1
        try:
            tool_call_positions, modified_text = self._find_tool_calls(text)
            results = []
            print(f"Found {len(tool_call_positions)} tool call(s) to execute")

            def execute_single_tool(i, start_pos, end_pos):
                """Execute a single tool in a separate thread."""
                print(f"Executing tool call {i + 1} at positions {start_pos}-{end_pos}")
                result = self._execute_tool_call(modified_text, start_pos, end_pos)
                return (i, result if result else f"Tool call {i + 1} execution failed")

            max_workers = min(len(tool_call_positions), 3)
            if tool_call_positions:
                try:
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=max_workers,
                    ) as executor:
                        futures = []
                        for i, (start_pos, end_pos) in enumerate(tool_call_positions):
                            future = executor.submit(
                                execute_single_tool,
                                i,
                                start_pos,
                                end_pos,
                            )
                            futures.append(future)
                        results_dict = {}
                        for future in concurrent.futures.as_completed(
                            futures,
                            timeout=60,
                        ):
                            try:
                                i, result = future.result(timeout=10)
                                results_dict[i] = result
                            except Exception as e:
                                print(f"Tool execution failed: {e}")
                                for idx, f in enumerate(futures):
                                    if f == future:
                                        results_dict[
                                            idx
                                        ] = f"Tool execution error: {str(e)}"
                                        break
                        for i in sorted(results_dict.keys()):
                            results.append(results_dict[i])
                except concurrent.futures.TimeoutError:
                    print("Tool execution timed out")
                    results.append("Tool execution timed out")
                except Exception as e:
                    print(f"Error in parallel tool execution: {e}")
                    for i, (start_pos, end_pos) in enumerate(tool_call_positions):
                        print(f"Fallback: executing tool call {i + 1} sequentially")
                        result = self._execute_tool_call(
                            modified_text,
                            start_pos,
                            end_pos,
                        )
                        if result:
                            results.append(result)
                        else:
                            results.append(f"Tool call {i + 1} execution failed")
            return results
        finally:
            with self._tool_execution_lock:
                self._pending_tool_executions = max(
                    0,
                    self._pending_tool_executions - 1,
                )

    def _graceful_connection_close(self, response) -> None:
        """Gracefully close the HTTP connection."""
        try:
            if response is not None:
                try:
                    remaining_data = response.raw.read(1024)
                    if remaining_data:
                        print(f"Consumed {len(remaining_data)} bytes of remaining data")
                except:
                    pass
                response.close()
                print("HTTP connection closed gracefully")
                time.sleep(0.1)
        except Exception as e:
            print(f"Error during graceful connection close: {e}")

    def _clean_summary(self, summary: str) -> str:
        """Clean and format the summary text."""
        cleaned = summary.strip().replace("\n", " ")
        cleaned = cleaned.replace('"', "").replace("'", "")
        cleaned = cleaned[:50]
        if not cleaned:
            return "Chat Summary"
        cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else cleaned
        return cleaned

    def _handle_summary_response(self, summary_queue: queue.Queue) -> None:
        try:
            response = summary_queue.get(timeout=30)
            self.parent.master.after(
                0,
                lambda: self.parent.update_tab_name(self, response.strip()),
            )
        except queue.Empty:
            self.parent.master.after(
                0,
                lambda: self.parent.update_tab_name(self, "Chat Summary"),
            )

    def fetch_summary_response(self, summary_queue: queue.Queue) -> None:
        """Fetch a summary of the conversation with retry logic and proper error handling."""
        try:
            questions, answers, _ = self.chat_state.get_safe_copy()
            max_retries = 3
            retry_delay = 1.0
            for retry in range(max_retries):
                if (
                    questions
                    and answers
                    and questions[0].strip()
                    and answers[0].strip()
                ):
                    break
                if retry < max_retries - 1:
                    print(f"Waiting for content to be ready (attempt {retry + 1})")
                    time.sleep(retry_delay)
                    questions, answers, _ = self.chat_state.get_safe_copy()
                else:
                    print("No valid content available for summary after retries")
                    summary_queue.put("Chat Summary")
                    return
            first_q = questions[0]
            first_a = answers[0][:500]
            summary_prompt = f"Please provide a very brief summary (3-5 words) of this conversation (no period):\n\nQ: {first_q}\nA: {first_a}"
            messages = [{"role": "user", "content": summary_prompt}]
            selected_model = self.parent.get_selected_model()
            payload = {"model": selected_model, "messages": messages, "stream": True}
            print(f"Requesting summary with model: {selected_model}")
            with requests.post(
                BASE_URL,
                json=payload,
                stream=True,
                timeout=30,
            ) as response:
                if response.status_code != 200:
                    error_msg = f"Summary API error: Status {response.status_code}"
                    print(f"{error_msg}\nResponse: {response.text}")
                    summary_queue.put("Chat Summary")
                    return
                accumulated_summary = ""
                done_received = False
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        data = json.loads(line.strip())
                        if "message" in data and "content" in data["message"]:
                            content_chunk = data["message"]["content"]
                            if content_chunk:
                                accumulated_summary += content_chunk
                        if data.get("done", False):
                            done_received = True
                            summary = self._clean_summary(accumulated_summary)
                            print(f"Summary generated: {summary}")
                            summary_queue.put(summary)
                            return
                    except json.JSONDecodeError as json_err:
                        print(
                            f"Failed to decode JSON line in summary: {line}. Error: {json_err}",
                        )
                        continue
                    except Exception as content_err:
                        print(f"Error processing summary content chunk: {content_err}")
                        continue
                if accumulated_summary:
                    summary = self._clean_summary(accumulated_summary)
                    print(f"Summary generated (no done flag): {summary}")
                    summary_queue.put(summary)
                else:
                    print("No summary content received")
                    summary_queue.put("Chat Summary")
        except requests.exceptions.Timeout:
            print("Summary request timed out")
            summary_queue.put("Chat Summary")
        except requests.exceptions.ConnectionError:
            print("Summary connection error - is Ollama running?")
            summary_queue.put("Chat Summary")
        except requests.exceptions.RequestException as req_err:
            print(f"Summary request error: {req_err}")
            summary_queue.put("Chat Summary")
        except Exception as e:
            print(f"Error fetching summary: {e}")
            import traceback

            traceback.print_exc()
            summary_queue.put("Chat Summary")

    def _add_newlines_to_long_content(
        self,
        content: str,
        chars_since_last_newline: int,
        threshold: int,
    ) -> str:
        """
        Add newlines to long content blocks that don't have natural line breaks.
        Preserves tool invocations and JSON structures. Ignores escaped newlines.

        Args:
            content: The original content chunk
            chars_since_last_newline: Number of characters since the last newline (accumulated across chunks)
            threshold: Maximum characters before forcing a newline

        Returns:
            Content with appropriate newlines added
        """
        if not content:
            return content
        if self._contains_actual_newline(content):
            return content
        json_patterns = [
            '"tool_call"',
            '"arguments"',
            '"name"',
            "{",
            "}",
            '"content"',
            '"text"',
        ]
        for pattern in json_patterns:
            if pattern in content:
                return content
        if "```" in content or "`" in content:
            return content
        if chars_since_last_newline >= threshold:
            result = ["\n"]
            current_line_length = 0
            remaining_content = content
        else:
            result = []
            current_line_length = chars_since_last_newline
            remaining_content = content
        while remaining_content:
            chars_available = threshold - current_line_length
            if chars_available <= 0:
                result.append("\n")
                current_line_length = 0
                chars_available = threshold
            if len(remaining_content) <= chars_available:
                result.append(remaining_content)
                break
            else:
                break_point = chars_available
                for i in range(min(50, chars_available), 0, -1):
                    char_pos = chars_available - i
                    if (
                        char_pos < len(remaining_content)
                        and remaining_content[char_pos] in " \t.,;:!?"
                    ):
                        break_point = char_pos + 1
                        break
                chunk = remaining_content[:break_point]
                result.append(chunk)
                result.append("\n")
                remaining_content = remaining_content[break_point:]
                current_line_length = 0
        return "".join(result)

    def _contains_actual_newline(self, content: str) -> bool:
        """
        Check if content contains actual newlines (not escaped ones like \\n).

        Args:
            content: The content to check

        Returns:
            True if content contains actual newline characters, False otherwise
        """
        if "\n" not in content:
            return False
        pos = 0
        while pos < len(content):
            newline_pos = content.find("\n", pos)
            if newline_pos == -1:
                break
            if newline_pos == 0:
                return True
            backslash_count = 0
            check_pos = newline_pos - 1
            while check_pos >= 0 and content[check_pos] == "\\":
                backslash_count += 1
                check_pos -= 1
            if backslash_count % 2 == 0:
                return True
            pos = newline_pos + 1
        return False

    def _find_tool_calls(self, text: str) -> tuple[list[tuple[int, int]], str]:
        """Find all tool calls in text and return their positions and potentially modified text."""
        internal_positions = self._find_internal_tool_calls(text)
        if internal_positions:
            return (internal_positions, text)
        return ([], text)

    def _find_internal_tool_calls(self, text: str) -> list[tuple[int, int]]:
        """Find all internal format tool calls in text and return their start and end positions."""
        tool_calls = []
        print(f"DEBUG: Searching for tool calls in text: {text[:200]}...")
        if '"tool_call"' in text:
            print("DEBUG: Found 'tool_call' string in text")
        else:
            print("DEBUG: No 'tool_call' string found in text")
            return []
        try:
            cleaned_text = text.replace("\n", " ")
            texts_to_try = [text, cleaned_text]
            for text_variant in texts_to_try:
                pattern = '\\{\\s*"tool_call"\\s*:\\s*\\{'
                for match in re.finditer(pattern, text_variant):
                    start_pos = match.start()
                    brace_count = 0
                    in_string = False
                    escape_next = False
                    for i, char in enumerate(text_variant[start_pos:], start_pos):
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
                                    end_pos = i + 1
                                    json_candidate = text_variant[start_pos:end_pos]
                                    json_candidate_clean = re.sub(
                                        "\\s+",
                                        " ",
                                        json_candidate,
                                    )
                                    try:
                                        parsed = json.loads(json_candidate_clean)
                                        if (
                                            isinstance(parsed, dict)
                                            and "tool_call" in parsed
                                            and isinstance(parsed["tool_call"], dict)
                                            and ("name" in parsed["tool_call"])
                                        ):
                                            print(
                                                f"DEBUG: Found valid tool call: {parsed['tool_call']['name']}",
                                            )
                                            if text_variant == cleaned_text:
                                                original_start = text.find(
                                                    '{"tool_call"',
                                                )
                                                if original_start == -1:
                                                    original_start = text.find(
                                                        '{ "tool_call"',
                                                    )
                                                if original_start != -1:
                                                    orig_brace_count = 0
                                                    orig_in_string = False
                                                    orig_escape_next = False
                                                    for j, orig_char in enumerate(
                                                        text[original_start:],
                                                        original_start,
                                                    ):
                                                        if orig_escape_next:
                                                            orig_escape_next = False
                                                            continue
                                                        if (
                                                            orig_char == "\\"
                                                            and orig_in_string
                                                        ):
                                                            orig_escape_next = True
                                                            continue
                                                        if orig_char == '"' and (
                                                            not orig_escape_next
                                                        ):
                                                            orig_in_string = (
                                                                not orig_in_string
                                                            )
                                                            continue
                                                        if not orig_in_string:
                                                            if orig_char == "{":
                                                                orig_brace_count += 1
                                                            elif orig_char == "}":
                                                                orig_brace_count -= 1
                                                                if (
                                                                    orig_brace_count
                                                                    == 0
                                                                ):
                                                                    tool_calls.append(
                                                                        (
                                                                            original_start,
                                                                            j + 1,
                                                                        ),
                                                                    )
                                                                    break
                                            else:
                                                tool_calls.append((start_pos, end_pos))
                                    except json.JSONDecodeError:
                                        if self._try_fix_incomplete_json(
                                            json_candidate_clean,
                                        ):
                                            json_candidate_fixed = (
                                                json_candidate_clean + "}"
                                            )
                                            try:
                                                parsed_fixed = json.loads(
                                                    json_candidate_fixed,
                                                )
                                                if (
                                                    isinstance(parsed_fixed, dict)
                                                    and "tool_call" in parsed_fixed
                                                    and isinstance(
                                                        parsed_fixed["tool_call"],
                                                        dict,
                                                    )
                                                    and (
                                                        "name"
                                                        in parsed_fixed["tool_call"]
                                                    )
                                                ):
                                                    print(
                                                        f"DEBUG: Recovered tool call by adding missing closing brace",
                                                    )
                                                    if text_variant == cleaned_text:
                                                        original_start = text.find(
                                                            '{"tool_call"',
                                                        )
                                                        if original_start == -1:
                                                            original_start = text.find(
                                                                '{ "tool_call"',
                                                            )
                                                        if original_start != -1:
                                                            orig_brace_count = 0
                                                            orig_in_string = False
                                                            orig_escape_next = False
                                                            last_brace_pos = (
                                                                original_start
                                                            )
                                                            for (
                                                                j,
                                                                orig_char,
                                                            ) in enumerate(
                                                                text[original_start:],
                                                                original_start,
                                                            ):
                                                                if orig_escape_next:
                                                                    orig_escape_next = (
                                                                        False
                                                                    )
                                                                    continue
                                                                if (
                                                                    orig_char == "\\"
                                                                    and orig_in_string
                                                                ):
                                                                    orig_escape_next = (
                                                                        True
                                                                    )
                                                                    continue
                                                                if (
                                                                    orig_char == '"'
                                                                    and (
                                                                        not orig_escape_next
                                                                    )
                                                                ):
                                                                    orig_in_string = (
                                                                        not orig_in_string
                                                                    )
                                                                    continue
                                                                if not orig_in_string:
                                                                    if orig_char == "{":
                                                                        orig_brace_count += (
                                                                            1
                                                                        )
                                                                    elif (
                                                                        orig_char == "}"
                                                                    ):
                                                                        last_brace_pos = (
                                                                            j + 1
                                                                        )
                                                                        orig_brace_count -= (
                                                                            1
                                                                        )
                                                            tool_calls.append(
                                                                (
                                                                    original_start,
                                                                    last_brace_pos,
                                                                ),
                                                            )
                                                    else:
                                                        tool_calls.append(
                                                            (start_pos, end_pos),
                                                        )
                                            except json.JSONDecodeError:
                                                pass
                                    break
                if tool_calls:
                    break
            print(f"DEBUG: Total tool calls found: {len(tool_calls)}")
            return tool_calls
        except Exception as e:
            print(f"Error finding internal tool calls: {e}")
            return []

    def _find_and_convert_openai_tool_calls(
        self,
        text: str,
    ) -> tuple[list[tuple[int, int]], str]:
        """Find OpenAI format tool calls, convert to internal format, and return positions and modified text."""
        try:
            pattern = '\\{\\s*"tool_calls"\\s*:\\s*\\['
            for match in re.finditer(pattern, text):
                start_pos = match.start()
                brace_count = 0
                in_string = False
                escape_next = False
                for i, char in enumerate(text[start_pos:], start_pos):
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
                                end_pos = i + 1
                                json_str = text[start_pos:end_pos]
                                try:
                                    openai_data = json.loads(json_str)
                                    if (
                                        isinstance(openai_data, dict)
                                        and "tool_calls" in openai_data
                                        and isinstance(openai_data["tool_calls"], list)
                                        and (len(openai_data["tool_calls"]) > 0)
                                    ):
                                        tool_call = openai_data["tool_calls"][0]
                                        if (
                                            isinstance(tool_call, dict)
                                            and "function" in tool_call
                                            and isinstance(tool_call["function"], dict)
                                            and ("name" in tool_call["function"])
                                            and ("arguments" in tool_call["function"])
                                        ):
                                            if len(openai_data["tool_calls"]) > 1:
                                                print(
                                                    f"Warning: Found {len(openai_data['tool_calls'])} tool calls in OpenAI format, converting only the first one",
                                                )
                                            function = tool_call["function"]
                                            tool_name = function["name"]
                                            arguments_str = function["arguments"]
                                            try:
                                                arguments = json.loads(arguments_str)
                                            except json.JSONDecodeError as e:
                                                print(
                                                    f"Failed to parse arguments JSON: {e}",
                                                )
                                                continue
                                            internal_format = {
                                                "tool_call": {
                                                    "name": tool_name,
                                                    "arguments": arguments,
                                                },
                                            }
                                            internal_json = json.dumps(
                                                internal_format,
                                                indent=2,
                                            )
                                            internal_json_with_newline = (
                                                "\n" + internal_json
                                            )
                                            modified_text = (
                                                text[:start_pos]
                                                + internal_json_with_newline
                                                + text[end_pos:]
                                            )
                                            new_end_pos = start_pos + len(
                                                internal_json_with_newline,
                                            )
                                            print(
                                                f"Converted OpenAI tool call to internal format: {tool_name}",
                                            )
                                            return (
                                                [(start_pos, new_end_pos)],
                                                modified_text,
                                            )
                                except json.JSONDecodeError:
                                    continue
                                break
            return ([], text)
        except Exception as e:
            print(f"Error finding and converting OpenAI tool calls: {e}")
            return ([], text)

    def _try_fix_incomplete_json(self, json_str: str) -> bool:
        """Check if adding a closing brace would make valid JSON.

        First removes any invalid characters after the last } that shouldn't be there
        in valid JSON, then checks if adding one more } would make it valid.

        Args:
            json_str: The potentially incomplete JSON string

        Returns:
            True if adding a } would make valid JSON, False otherwise
        """
        if not json_str or not json_str.strip():
            return False
        cleaned = json_str.strip()
        last_brace_idx = cleaned.rfind("}")
        if last_brace_idx == -1:
            if "{" in cleaned and '"tool_call"' in cleaned:
                open_braces = cleaned.count("{")
                close_braces = cleaned.count("}")
                return open_braces > close_braces
            return False
        after_brace = cleaned[last_brace_idx + 1 :]
        valid_chars_after_brace = set(" \t\n\r,]}")
        cleaned_after = "".join(c for c in after_brace if c in valid_chars_after_brace)
        json_to_test = cleaned[: last_brace_idx + 1] + cleaned_after
        try:
            json.loads(json_to_test)
            return False
        except json.JSONDecodeError:
            pass
        open_braces = json_to_test.count("{")
        close_braces = json_to_test.count("}")
        if open_braces > close_braces:
            test_json = json_to_test.rstrip() + "}"
            try:
                parsed = json.loads(test_json)
                if (
                    isinstance(parsed, dict)
                    and "tool_call" in parsed
                    and isinstance(parsed["tool_call"], dict)
                    and ("name" in parsed["tool_call"])
                ):
                    return True
            except json.JSONDecodeError:
                pass
        return False

    def _filter_tool_calls_from_content(self, content: str) -> str:
        """Remove tool call JSON from content while preserving everything else.

        This ensures tool calls are not sent back to the LLM in conversation history,
        but tool results and other content are preserved.
        """
        if not content or (
            '{"tool_call"' not in content and '{"tool_calls"' not in content
        ):
            return content
        tool_calls, modified_content = self._find_tool_calls(content)
        if not tool_calls:
            return content
        tool_calls.sort(reverse=True)
        filtered_content = modified_content
        for start_pos, end_pos in tool_calls:
            adjusted_start = start_pos
            adjusted_end = end_pos
            while (
                adjusted_start > 0 and filtered_content[adjusted_start - 1] in " \n\r\t"
            ):
                adjusted_start -= 1
            newline_count = 0
            while (
                adjusted_end < len(filtered_content)
                and filtered_content[adjusted_end] in " \n\r\t"
            ):
                if filtered_content[adjusted_end] == "\n":
                    newline_count += 1
                    if newline_count > 1:
                        break
                adjusted_end += 1
            filtered_content = (
                filtered_content[:adjusted_start] + filtered_content[adjusted_end:]
            )
        import re

        filtered_content = re.sub("\\n{3,}", "\n\n", filtered_content)
        return filtered_content.strip()

    class _PositionState(Enum):
        """State of position computation"""

        COMPUTING = "computing"
        READY = "ready"
        ERROR = "error"

    class _PositionEntry:
        """Thread-safe entry for position tracking"""

        def __init__(self):
            self.state = ChatTabStreamingCore._PositionState.COMPUTING
            self.position = None
            self.event = threading.Event()

    def _init_position_manager(self):
        """Initialize the thread-safe position manager"""
        self._position_entries = {}
        self._position_lock = threading.RLock()

    def _final_highlight(self):
        """Perform final comprehensive highlighting after streaming is complete.

        Thread-safe implementation that ensures Tkinter operations run on main thread.
        """

        def perform_highlight():
            try:
                content = self.chat_display.get("1.0", tk.END)
                content_length = len(content)
                if content_length > 10000:
                    print(
                        f"Large content detected ({content_length} chars), using incremental highlighting",
                    )
                    self._incremental_highlight()
                else:
                    self.chat_display.last_highlighted_content = ""
                    self.chat_display.last_highlighted_length = 0
                    self.chat_display.highlight_text()
                    print("Final highlighting completed")
            except Exception as e:
                print(f"Error in final highlighting: {e}")

        if threading.current_thread() == threading.main_thread():
            perform_highlight()
        else:
            self.parent.master.after_idle(perform_highlight)

    def _was_at_bottom(self) -> bool:
        """Check if user was scrolled to bottom.

        Thread-safe implementation that ensures Tkinter operations run on main thread.
        """
        if threading.current_thread() == threading.main_thread():
            try:
                yview = self.chat_display.yview()
                return yview[1] >= 0.99
            except tk.TclError:
                return True
        else:
            result_queue = queue.Queue()

            def check_scroll():
                try:
                    yview = self.chat_display.yview()
                    result_queue.put(("success", yview[1] >= 0.99))
                except tk.TclError:
                    result_queue.put(("success", True))
                except Exception as e:
                    result_queue.put(("error", str(e)))

            self.parent.master.after_idle(check_scroll)
            try:
                status, result = result_queue.get(timeout=1)
                if status == "error":
                    print(f"Error checking scroll position: {result}")
                    return True
                return result
            except queue.Empty:
                print("Timeout checking scroll position")
                return True

    def _find_answer_position(self, answer_index: int) -> str:
        """
        Thread-safe implementation that prevents race conditions by ensuring
        only one thread computes the position for each answer_index.
        """
        entry = None
        should_compute = False
        with self._position_lock:
            if answer_index in self._position_entries:
                entry = self._position_entries[answer_index]
                if entry.state == self._PositionState.READY:
                    return entry.position
                if entry.state == self._PositionState.ERROR:
                    return "end"
            else:
                entry = self._PositionEntry()
                self._position_entries[answer_index] = entry
                should_compute = True
        if not should_compute:
            if entry.event.wait(timeout=10):
                with self._position_lock:
                    if entry.state == self._PositionState.READY:
                        return entry.position
            return "end"
        try:
            position = self._compute_answer_position_impl(answer_index)
            with self._position_lock:
                entry.state = self._PositionState.READY
                entry.position = position
                entry.event.set()
            return position
        except Exception as e:
            print(f"Error computing position for answer {answer_index}: {e}")
            with self._position_lock:
                entry.state = self._PositionState.ERROR
                entry.position = "end"
                entry.event.set()
            return "end"

    def _continue_after_tool_calls(
        self,
        answer_index: int,
        tool_results: list[str],
    ) -> None:
        """Automatically continue the conversation after tool calls are executed."""
        try:
            print(f"Starting continuation after tool calls for answer {answer_index}")
            if self.stop_streaming_flag.is_set():
                print("Continuation cancelled - stop flag set")
                return
            continuation_key = f"continuation_started_{answer_index}"
            should_add_separator = True
            if should_add_separator:
                continuation_separator = "\n\n-=-=-=-=-\n\n**Continuing conversation based on tool results:**\n\n"
                separator_update = ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=continuation_separator,
                    is_done=False,
                    is_error=False,
                )
                if self._put_content_update_with_retry(separator_update):
                    print(f"Added continuation separator for answer {answer_index}")
                else:
                    print(
                        f"Failed to add continuation separator for answer {answer_index}",
                    )
            self._make_continuation_request(answer_index)
        except Exception as e:
            print(f"Error in continuation after tool calls: {e}")
            import traceback

            traceback.print_exc()
            error_update = ContentUpdate(
                answer_index=answer_index,
                content_chunk=f"\n\n[Error starting continuation: {str(e)}]",
                is_done=True,
                is_error=True,
            )
            self._put_content_update_with_retry(error_update)

    def get_summary(self) -> None:
        """Get a summary of the conversation with improved error handling and timing."""
        if getattr(self, "summary_generated", False):
            print("Summary already generated, skipping")
            return
        self.summary_generated = True

        def start_summary_generation():
            """Start summary generation with proper timing and coordination."""
            try:
                time.sleep(3)
                questions, answers, _ = self.chat_state.get_safe_copy()
                if not (
                    questions
                    and answers
                    and questions[0].strip()
                    and answers[0].strip()
                ):
                    print("No valid content available for summary generation")
                    self.parent.master.after(
                        0,
                        lambda: self.parent.update_tab_name(self, "Chat Summary"),
                    )
                    return
                summary_queue = queue.Queue()
                fetch_thread = threading.Thread(
                    target=self.fetch_summary_response,
                    args=(summary_queue,),
                    daemon=True,
                )
                fetch_thread.start()
                time.sleep(0.1)
                handler_thread = threading.Thread(
                    target=self._handle_summary_response,
                    args=(summary_queue,),
                    daemon=True,
                )
                handler_thread.start()
            except Exception as e:
                print(f"Error starting summary generation: {e}")
                self.parent.master.after(
                    0,
                    lambda: self.parent.update_tab_name(self, "Chat Summary"),
                )

        threading.Thread(target=start_summary_generation, daemon=True).start()

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
        import re

        TOOL_RESULT_PATTERN = re.compile(
            "\\*\\*Tool \\d+ Result:\\*\\*\\n```\\n(.*?)\\n```",
            re.DOTALL,
        )
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
        MULTIPLE_NEWLINES_PATTERN = re.compile("\\n{3,}")
        clean_content = MULTIPLE_NEWLINES_PATTERN.sub("\\n\\n", clean_content).strip()
        return (clean_content, tool_results, tool_call_jsons)

    def __init__(self):
        """Initialize ChatTabStreamingPart1 with all required attributes."""
        self.content_update_queue = queue.Queue()
        self.input_queue = queue.Queue()
        self.answer_end_positions = {}
        self.stop_streaming_flag = threading.Event()
        self.is_streaming = False
        self.current_request_thread = None
        self.summary_generated = False
        self.chat_history_questions = []
        self.chat_history_answers = []
        self._queue_processor_running = False
        self.stream_completion_lock = threading.Lock()
        self._processor_lock = threading.Lock()
        self._pending_tool_executions = 0
        self._tool_execution_lock = threading.Lock()
        self._continuation_states = {}
        self._continuation_lock = threading.Lock()
        self._chars_since_last_newline = 0
        self._init_position_manager()
        self.renderer = None
        try:
            from enhanced_tool_progress_manager import StreamingConnectionManager

            self.connection_manager = StreamingConnectionManager()
        except ImportError:
            print("Warning: Enhanced connection management not available")
            self.connection_manager = None

    def _handle_tool_calls_and_continue(
        self,
        accumulated_content: str,
        answer_index: int,
    ) -> None:
        """Execute tool calls and start continuation request."""
        try:
            if not self.is_streaming:
                print(
                    f"WARNING: Attempted to handle tool calls when not streaming (answer {answer_index})",
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
                        return
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
                    tool_results = self._execute_all_tool_calls(accumulated_content)
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
                            if i > 0:
                                time.sleep(0.05)
                    print(
                        f"Tool execution completed for answer {answer_index}, starting continuation...",
                    )
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
            print(f"Error in handle_tool_calls_and_continue: {e}")
            import traceback

            traceback.print_exc()
            error_text = f"\n\n**Error:** {str(e)}"
            error_update = ContentUpdate(
                answer_index=answer_index,
                content_chunk=error_text,
                is_done=True,
                is_error=True,
            )
            self._put_content_update_with_retry(error_update)
