import json
import queue
import re
import threading
import time
import tkinter as tk
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import requests

from expansion_language import expand
from utils import ContentUpdate


BASE_URL: str = "http://localhost:11434/api/chat"


class ChatTabStreaming:
    """Streaming functionality for ChatTab - API requests, content processing, and tool calls."""

    def submit_message(self) -> str:
        """Submit message or stop streaming if currently streaming."""

        # If currently streaming, stop instead of submitting
        if self.is_streaming:
            self.stop_streaming()
            return "break"

        message = self.input_field.get("1.0", tk.END)
        if message.strip():
            # Set streaming state
            self.is_streaming = True
            self.update_submit_button_text()
            self.stop_streaming_flag.clear()

            # Add to ChatState
            answer_index = self.chat_state.add_question(message.strip())

            # Add the question to the display immediately using server mode
            self.chat_display.set_server_mode(True)
            self.chat_display.config(state=tk.NORMAL)

            if answer_index > 0:
                # Add separator for subsequent questions
                sep = "-" * 80
                self.chat_display.insert(tk.END, f"\n\n{sep}\n\n")

            # Add Q: and A: lines
            self.chat_display.insert(tk.END, f"Q: {message.strip()}\n")
            self.chat_display.insert(tk.END, f"A:\n")

            # Disable server mode after structural content
            self.chat_display.set_server_mode(False)

            # Track where this answer starts (at the end of the "A: " line)
            answer_start_pos = self.chat_display.index(tk.END + " -1c")
            self.answer_end_positions[answer_index] = answer_start_pos

            # Disable editing during streaming
            self.chat_display.config(state=tk.DISABLED)

            # Start the queue processor if needed
            self._start_processor_if_needed()

            # Update legacy lists for backward compatibility during transition
            self.chat_history_questions = self.chat_state.questions.copy()
            self.chat_history_answers = self.chat_state.answers.copy()

            # Pass the specific index to the API thread
            self.input_queue.put(
                {
                    "model": self.parent.preferences["default_model"],
                    "prompt": expand(message),
                    "answer_index": answer_index,
                    "chat_history_questions": self.chat_state.questions[:-1],
                    "chat_history_answers": self.chat_state.answers[:-1],
                },
            )

            # Start API request thread and store reference
            self.current_request_thread = threading.Thread(
                target=self.fetch_api_response,
                args=(answer_index,),
                daemon=True,
            )
            self.current_request_thread.start()

            # Clear input field
            self.input_field.delete("1.0", tk.END)

        return "break"

    def stop_streaming(self) -> None:
        """Stop the current streaming request."""
        print("Stopping streaming...")

        # Set the stop flag
        self.stop_streaming_flag.set()

        # Stop the queue processor
        self._stop_processor()

        # Clear any pending queue items
        while not self.content_update_queue.empty():
            try:
                self.content_update_queue.get_nowait()
            except queue.Empty:
                break

        # Add a stopped message to the current answer
        if self.chat_state.is_streaming():
            current_answer_index = len(self.chat_state.answers) - 1
            if current_answer_index >= 0:
                stop_message = "\n\n[Streaming stopped by user]"
                self.chat_state.append_to_answer(current_answer_index, stop_message)

                # Update display with server mode
                self.chat_display.set_server_mode(True)
                self.chat_display.config(state=tk.NORMAL)
                self._insert_content_at_answer(current_answer_index, stop_message)
                self.chat_display.set_server_mode(False)
                self.chat_display.config(state=tk.NORMAL)  # Re-enable for normal use

        # Finish streaming state
        self.chat_state.finish_streaming()
        self.is_streaming = False
        self.update_submit_button_text()
        self.current_request_thread = None

        # Final highlight
        self.parent.master.after(100, self._final_highlight)

    def process_content_queue(self) -> None:
        """Process queue with smart highlighting throttling and proper termination."""

        # Check if we should still be running
        with self._processor_lock:
            if not self._queue_processor_running:
                print("Queue processor stopping - flag is False")
                return

        updates_processed = 0
        streaming_finished = False
        content_accumulated = 0
        last_highlight_time = time.time()
        has_pending_updates = False

        # Newline tracking - track characters across ALL content chunks
        chars_since_newline = getattr(self, "_chars_since_last_newline", 0)
        NEWLINE_THRESHOLD = 900

        # Highlighting thresholds
        HIGHLIGHT_MIN_INTERVAL = 0.5  # Minimum 500ms between highlights
        HIGHLIGHT_CONTENT_THRESHOLD = 300  # Or every 300 characters
        HIGHLIGHT_UPDATE_THRESHOLD = 25  # Or every 25 updates

        try:
            # Process all available updates
            while True:
                try:
                    update = self.content_update_queue.get_nowait()
                    has_pending_updates = True

                    # Update ChatState (for data integrity)
                    if update.is_error:
                        error_content = f"\n\n[Error: {update.content_chunk}]"
                        self.chat_state.append_to_answer(
                            update.answer_index,
                            error_content,
                        )
                        self.chat_state.finish_streaming()
                        content_to_insert = error_content
                        streaming_finished = True
                        # Reset character counter on error
                        chars_since_newline = 0
                    else:
                        original_content = update.content_chunk

                        # NEW: Break up long content blocks that don't have newlines
                        content_to_insert = self._add_newlines_to_long_content(
                            original_content,
                            chars_since_newline,  # Pass current accumulated count
                            NEWLINE_THRESHOLD,
                        )

                        # Update character counter based on the processed content
                        if "\n" in content_to_insert:
                            # Find the last newline and count characters after it
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

                    # Store the updated character count as instance variable
                    self._chars_since_last_newline = chars_since_newline

                    # Directly update the text widget
                    self._insert_content_at_answer(
                        update.answer_index,
                        content_to_insert,
                    )
                    updates_processed += 1
                    content_accumulated += len(content_to_insert)

                    # Smart highlighting decision
                    current_time = time.time()
                    time_since_last_highlight = current_time - last_highlight_time

                    should_highlight = (
                        # Time-based: At least 500ms have passed
                        time_since_last_highlight >= HIGHLIGHT_MIN_INTERVAL
                        or
                        # Content-based: Accumulated enough content
                        content_accumulated >= HIGHLIGHT_CONTENT_THRESHOLD
                        or
                        # Update-based: Processed enough updates
                        updates_processed >= HIGHLIGHT_UPDATE_THRESHOLD
                        or
                        # Always highlight when streaming is done
                        update.is_done
                    )

                    if should_highlight:
                        self.chat_display.highlight_text()
                        last_highlight_time = current_time
                        content_accumulated = 0  # Reset accumulator

                    # Handle summary generation on completion
                    if (
                        update.is_done
                        and update.answer_index == 0
                        and not self.summary_generated
                    ):
                        self.summary_generated = True
                        self.parent.master.after(1000, self.get_summary)

                    # If streaming is finished, we can exit the processing loop
                    if streaming_finished:
                        break

                except queue.Empty:
                    # No more updates to process right now
                    break

            # Final highlight only if we processed updates and haven't highlighted recently
            current_time = time.time()
            if (
                updates_processed > 0
                and (current_time - last_highlight_time) >= HIGHLIGHT_MIN_INTERVAL
            ):
                self.chat_display.highlight_text()

            # Determine if we should continue processing
            if streaming_finished:
                # Streaming is completely done, finish up
                self._finish_streaming()
                return

            # Continue processing - we need to keep running until we get the "done" signal
            with self._processor_lock:
                if self._queue_processor_running:
                    # Always continue if we're supposed to be running and haven't received "done"
                    # Use adaptive delay: shorter if we just processed updates, longer if idle
                    delay = 100 if has_pending_updates else 200
                    self.parent.master.after(delay, self.process_content_queue)
                else:
                    print("Queue processor stopping - manually stopped")

        except Exception as e:
            print(f"Error in queue processor: {e}")
            import traceback

            traceback.print_exc()
            self._finish_streaming()

    def _add_newlines_to_long_content(
        self,
        content: str,
        chars_since_last_newline: int,
        threshold: int,
    ) -> str:
        """
        Add newlines to long content blocks that don't have natural line breaks.

        Args:
            content: The original content chunk
            chars_since_last_newline: Number of characters since the last newline (accumulated across chunks)
            threshold: Maximum characters before forcing a newline

        Returns:
            Content with appropriate newlines added
        """
        if not content:
            return content

        # If content already has newlines, return as-is
        if "\n" in content:
            return content

        # If we're already over threshold, start with a newline
        if chars_since_last_newline >= threshold:
            result = ["\n"]
            current_line_length = 0
            remaining_content = content
        else:
            result = []
            current_line_length = chars_since_last_newline
            remaining_content = content

        while remaining_content:
            # Calculate how many characters we can add to current line
            chars_available = threshold - current_line_length

            if chars_available <= 0:
                # We're already at threshold, add newline and reset
                result.append("\n")
                current_line_length = 0
                chars_available = threshold

            # Take as much content as we can for this line
            if len(remaining_content) <= chars_available:
                # Remaining content fits in current line
                result.append(remaining_content)
                break
            else:
                # Need to break the content
                # Try to break at a word boundary if possible
                break_point = chars_available
                for i in range(
                    min(50, chars_available),
                    0,
                    -1,
                ):  # Look back up to 50 chars
                    char_pos = chars_available - i
                    if (
                        char_pos < len(remaining_content)
                        and remaining_content[char_pos] in " \t.,;:!?"
                    ):
                        break_point = char_pos + 1  # Include the delimiter
                        break

                # Take the chunk up to break point
                chunk = remaining_content[:break_point]
                result.append(chunk)
                result.append("\n")

                # Update remaining content and reset line length
                remaining_content = remaining_content[break_point:]
                current_line_length = 0

        return "".join(result)

    def _finish_streaming(self):
        """Clean finish with single final highlight."""
        print("Finishing streaming and stopping processor")
        self._stop_processor()

        # Ensure server mode is disabled
        self.chat_display.set_server_mode(False)
        self.chat_display.config(state=tk.NORMAL)

        # Reset streaming state
        self.is_streaming = False
        self.update_submit_button_text()
        self.current_request_thread = None

        # Only ONE final comprehensive highlight
        self.parent.master.after(200, self._final_highlight)

    def _stop_processor(self):
        """Atomically stop the queue processor."""
        with self._processor_lock:
            if self._queue_processor_running:
                print("Queue processor stopped")
                self._queue_processor_running = False

    def _start_processor_if_needed(self):
        """Only start processor if not already running."""
        with self._processor_lock:
            if not self._queue_processor_running:
                self._queue_processor_running = True
                print(f"Starting queue processor for tab")
                # Start immediately, don't use after()
                self.parent.master.after(0, self.process_content_queue)
                return True
            else:
                print("Queue processor already running")
        return False

    def _final_highlight(self):
        """Perform final comprehensive highlighting after streaming is complete."""
        try:
            # Force a complete re-highlight by resetting the highlighting state
            self.chat_display.last_highlighted_content = ""
            self.chat_display.last_highlighted_length = 0
            self.chat_display.highlight_text()
            print("Final highlighting completed")
        except Exception as e:
            print(f"Error in final highlighting: {e}")

    def _insert_content_at_answer(self, answer_index: int, content: str) -> None:
        """Directly insert content at the end of a specific answer."""
        if not content:
            return

        # Enable server mode to prevent undo recording
        self.chat_display.set_server_mode(True)

        # Enable editing
        self.chat_display.config(state=tk.NORMAL)

        # Find or calculate the insertion position for this answer
        if answer_index in self.answer_end_positions:
            # We know where this answer ends, insert there
            insert_pos = self.answer_end_positions[answer_index]
        else:
            # First content for this answer, find the "A: " line
            insert_pos = self._find_answer_position(answer_index)

        # Insert the content
        self.chat_display.insert(insert_pos, content)

        # Update the end position for this answer
        self.answer_end_positions[answer_index] = f"{insert_pos} + {len(content)}c"

        # Scroll to bottom if user was already at bottom
        if self._was_at_bottom():
            self.chat_display.see(tk.END)

        # Disable server mode after insertion
        self.chat_display.set_server_mode(False)

    def _find_answer_position(self, answer_index: int) -> str:
        """Find the position where we should insert content for a specific answer."""
        if answer_index in self.answer_end_positions:
            return self.answer_end_positions[answer_index]

        # Search for the answer line in the text
        text_content = self.chat_display.get("1.0", tk.END)
        lines = text_content.split("\n")

        answer_count = 0
        for i, line in enumerate(lines):
            if line.startswith("A: "):
                if answer_count == answer_index:
                    # Found our answer line, position at the end of this line
                    line_pos = f"{i + 1}.end"
                    self.answer_end_positions[answer_index] = line_pos
                    return line_pos
                answer_count += 1

        # Fallback: if we can't find the specific answer, append to end
        end_pos = self.chat_display.index(tk.END + " -1c")
        self.answer_end_positions[answer_index] = end_pos
        return end_pos

    def _was_at_bottom(self) -> bool:
        """Check if user was scrolled to bottom."""
        try:
            yview = self.chat_display.yview()
            return yview[1] >= 0.99
        except tk.TclError:
            return True  # Default to True if we can't determine scroll position

    def _find_tool_calls(self, text: str) -> list[tuple[int, int]]:
        """Find all tool calls in text and return their start and end positions."""
        tool_calls = []

        try:
            # Pattern to find tool call opening
            pattern = r'\{\s*"tool_call"\s*:\s*\{'

            for match in re.finditer(pattern, text):
                start_pos = match.start()

                # Use character-by-character parsing to find the complete JSON
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

                    if char == '"' and not escape_next:
                        in_string = not in_string
                        continue

                    if not in_string:
                        if char == "{":
                            brace_count += 1
                        elif char == "}":
                            brace_count -= 1
                            if brace_count == 0:
                                # Found complete JSON object, validate it
                                end_pos = i + 1
                                json_candidate = text[start_pos:end_pos]
                                try:
                                    parsed = json.loads(json_candidate)
                                    if (
                                        isinstance(parsed, dict)
                                        and "tool_call" in parsed
                                        and isinstance(parsed["tool_call"], dict)
                                        and "name" in parsed["tool_call"]
                                    ):
                                        tool_calls.append((start_pos, end_pos))
                                except json.JSONDecodeError:
                                    pass
                                break

            return tool_calls

        except Exception as e:
            print(f"Error finding tool calls: {e}")
            return []

    def _contains_tool_call(self, text: str) -> bool:
        """Check if text contains any complete tool calls."""
        return len(self._find_tool_calls(text)) > 0

    def _execute_tool_call(
        self,
        text: str,
        start_pos: int,
        end_pos: int,
    ) -> str | None:
        """Execute a specific tool call at the given positions and return the result."""
        try:
            json_str = text[start_pos:end_pos]
            tool_call_data = json.loads(json_str)

            if "tool_call" not in tool_call_data:
                return "Error: Invalid tool call format"

            tool_call = tool_call_data["tool_call"]
            tool_name = tool_call["name"]
            arguments = tool_call.get("arguments", {})

            print(f"Executing tool: {tool_name} with args: {arguments}")

            # Parse server name and tool name
            if "_" in tool_name:
                server_name, actual_tool_name = tool_name.split("_", 1)
            else:
                return f"Error: Invalid tool name format: {tool_name}"

            print(f"Server name: {server_name}, Tool name: {actual_tool_name}")

            # Get MCP manager from parent
            mcp_manager = getattr(self.parent, "mcp_manager", None)
            main_loop = getattr(self.parent, "event_loop", None)

            # Execute tool call with simplified async handling
            if mcp_manager and main_loop:
                try:
                    # Run the coroutine in the main loop from this thread
                    import asyncio

                    future = asyncio.run_coroutine_threadsafe(
                        mcp_manager.call_tool(server_name, actual_tool_name, arguments),
                        main_loop,
                    )
                    result = future.result(timeout=30)

                    return (
                        json.dumps(result, indent=2)
                        if result
                        else "Tool execution completed"
                    )

                except Exception as e:
                    print(f"Detailed error: {type(e).__name__}: {str(e)}")
                    import traceback

                    traceback.print_exc()
                    return f"Error executing tool: {str(e)}"

            return "MCP Manager not available"

        except json.JSONDecodeError as e:
            print(f"JSON decode error in tool call: {str(e)}")
            return f"Error parsing tool call JSON: {str(e)}"
        except Exception as e:
            print(f"Error parsing tool call: {str(e)}")
            import traceback

            traceback.print_exc()
            return f"Error parsing tool call: {str(e)}"

    def _execute_all_tool_calls(self, text: str) -> list[str]:
        """Execute all tool calls found in the text and return their results."""
        tool_calls = self._find_tool_calls(text)
        results = []

        print(f"Found {len(tool_calls)} tool call(s) to execute")

        for i, (start_pos, end_pos) in enumerate(tool_calls):
            print(f"Executing tool call {i + 1} at positions {start_pos}-{end_pos}")
            result = self._execute_tool_call(text, start_pos, end_pos)
            if result:
                results.append(result)
            else:
                results.append(f"Tool call {i + 1} execution failed")

        return results

    def fetch_api_response(self, answer_index: int) -> None:
        """Fetch API response for a specific answer index using queue-based updates."""
        try:
            # Check if we should stop before starting
            if self.stop_streaming_flag.is_set():
                print(f"Streaming stopped before API request for answer {answer_index}")
                return

            self.parent.check_mcp_status()
            # Get the data payload for this request
            data_payload: dict[str, Any] = self.input_queue.get(timeout=3)
            if data_payload is None:
                return

            # Validate that we have the correct answer index
            payload_answer_index = data_payload.get("answer_index")
            if payload_answer_index != answer_index:
                print(
                    f"Warning: Answer index mismatch. Expected {answer_index}, got {payload_answer_index}",
                )
                return

            # Prepare the messages for the Ollama API
            messages: list[dict[str, str]] = []

            # Add chat history (excluding the current question/answer pair)
            for q, a in zip(
                data_payload["chat_history_questions"],
                data_payload["chat_history_answers"],
            ):
                if q.strip() and a.strip():  # Only add complete Q&A pairs
                    expanded_q = expand(q)
                    messages.append({"role": "user", "content": expanded_q})
                    messages.append({"role": "assistant", "content": a})

            # Add the current question
            messages.append({"role": "user", "content": data_payload["prompt"]})

            # Get available MCP tools from parent
            available_tools = self.parent.get_available_mcp_tools()

            # Prepare Ollama API payload
            ollama_payload: dict[str, Any] = {
                "model": data_payload["model"],
                "messages": messages,
                "stream": True,
            }

            # Add tools if available
            if available_tools:
                ollama_payload["tools"] = available_tools

            print(f"Starting API request for answer index {answer_index}")
            print(f"Tools included: {len(available_tools)} tools")

            # Initialize content accumulator for tool call detection
            accumulated_content = ""

            # Make the streaming API request
            with requests.post(
                BASE_URL,
                json=ollama_payload,
                stream=True,
                timeout=30,
            ) as response:

                if response.status_code != 200:
                    error_msg = f"API Error: Status code {response.status_code}"
                    print(error_msg)
                    # Queue error update
                    self.content_update_queue.put(
                        ContentUpdate(
                            answer_index=answer_index,
                            content_chunk=error_msg,
                            is_done=True,
                            is_error=True,
                        ),
                    )
                    return

                for line in response.iter_lines(decode_unicode=True):
                    # Check for stop flag during streaming
                    if self.stop_streaming_flag.is_set():
                        print(
                            f"Streaming stopped during API response for answer {answer_index}",
                        )
                        return

                    if not line:
                        continue

                    try:
                        data = json.loads(line.strip())

                        # print(data)
                        if "message" in data and "content" in data["message"]:
                            content_chunk = data["message"]["content"]
                            if content_chunk:
                                # Check stop flag before queuing content
                                if self.stop_streaming_flag.is_set():
                                    print(
                                        f"Streaming stopped while processing content for answer {answer_index}",
                                    )
                                    return

                                # Accumulate content for tool call detection
                                accumulated_content += content_chunk

                                # Queue content update
                                self.content_update_queue.put(
                                    ContentUpdate(
                                        answer_index=answer_index,
                                        content_chunk=content_chunk,
                                        is_done=False,
                                        is_error=False,
                                    ),
                                )

                        if data.get("done", False):
                            # Check stop flag before marking as done
                            if self.stop_streaming_flag.is_set():
                                print(
                                    f"Streaming stopped before completion for answer {answer_index}",
                                )
                                return

                            # Check for tool calls in accumulated content
                            tool_calls = self._find_tool_calls(accumulated_content)
                            if tool_calls:
                                print(
                                    f"Found {len(tool_calls)} tool call(s) in response for answer {answer_index}",
                                )

                                try:
                                    # Execute all tool calls
                                    tool_results = self._execute_all_tool_calls(
                                        accumulated_content,
                                    )

                                    # Add all tool results to the response
                                    for i, tool_result in enumerate(tool_results):
                                        if tool_result:
                                            try:
                                                # Try to parse as JSON first
                                                data_result = json.loads(tool_result)
                                                if (
                                                    isinstance(data_result, dict)
                                                    and "content" in data_result
                                                ):
                                                    if (
                                                        isinstance(
                                                            data_result["content"],
                                                            list,
                                                        )
                                                        and len(data_result["content"])
                                                        > 0
                                                    ):
                                                        value = data_result["content"][
                                                            0
                                                        ].get("text", str(data_result))
                                                    else:
                                                        value = str(
                                                            data_result["content"],
                                                        )
                                                else:
                                                    value = json.dumps(
                                                        data_result,
                                                        indent=2,
                                                    )
                                            except (
                                                json.JSONDecodeError,
                                                KeyError,
                                                TypeError,
                                            ):
                                                # If not valid JSON or doesn't have expected structure, use as-is
                                                value = str(tool_result)

                                            result_text = f"\n\n**Tool {i + 1} Result:**\n```\n{value}\n```"

                                            # Queue the tool result
                                            self.content_update_queue.put(
                                                ContentUpdate(
                                                    answer_index=answer_index,
                                                    content_chunk=result_text,
                                                    is_done=False,
                                                    is_error=False,
                                                ),
                                            )

                                    print(
                                        f"All {len(tool_results)} tool results added for answer {answer_index}",
                                    )

                                    # Automatically continue the conversation after tool calls
                                    self._continue_after_tool_calls(
                                        answer_index,
                                        tool_results,
                                    )
                                    return  # Don't mark as done yet, we're continuing

                                except Exception as tool_error:
                                    print(f"Error executing tool calls: {tool_error}")
                                    import traceback

                                    traceback.print_exc()

                                    # Queue error message
                                    error_text = f"\n\n**Tool Execution Error:** {str(tool_error)}"
                                    self.content_update_queue.put(
                                        ContentUpdate(
                                            answer_index=answer_index,
                                            content_chunk=error_text,
                                            is_done=False,
                                            is_error=False,
                                        ),
                                    )

                            # Queue completion update (only if no tool calls were processed)
                            self.content_update_queue.put(
                                ContentUpdate(
                                    answer_index=answer_index,
                                    content_chunk="",
                                    is_done=True,
                                    is_error=False,
                                ),
                            )
                            break

                    except json.JSONDecodeError:
                        # Skip malformed JSON lines
                        continue
                    except Exception as content_err:
                        print(f"Error processing content chunk: {content_err}")
                        continue

        except requests.exceptions.Timeout:
            error_msg = "Request timed out"
            print(f"API request timeout for answer index {answer_index}")
            if not self.stop_streaming_flag.is_set():
                self.content_update_queue.put(
                    ContentUpdate(
                        answer_index=answer_index,
                        content_chunk=error_msg,
                        is_done=True,
                        is_error=True,
                    ),
                )

        except requests.exceptions.ConnectionError:
            error_msg = "Connection error - is Ollama running?"
            print(f"Connection error for answer index {answer_index}")
            if not self.stop_streaming_flag.is_set():
                self.content_update_queue.put(
                    ContentUpdate(
                        answer_index=answer_index,
                        content_chunk=error_msg,
                        is_done=True,
                        is_error=True,
                    ),
                )

        except requests.exceptions.RequestException as req_err:
            error_msg = f"Request error: {str(req_err)}"
            print(f"Request exception for answer index {answer_index}: {req_err}")
            if not self.stop_streaming_flag.is_set():
                self.content_update_queue.put(
                    ContentUpdate(
                        answer_index=answer_index,
                        content_chunk=error_msg,
                        is_done=True,
                        is_error=True,
                    ),
                )

        except queue.Empty:
            print(f"No data payload available for answer index {answer_index}")

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(
                f"Unexpected error in fetch_api_response for answer index {answer_index}: {e}",
            )
            import traceback

            traceback.print_exc()
            if not self.stop_streaming_flag.is_set():
                self.content_update_queue.put(
                    ContentUpdate(
                        answer_index=answer_index,
                        content_chunk=error_msg,
                        is_done=True,
                        is_error=True,
                    ),
                )

        finally:
            # Always reset streaming state when this thread ends
            print(f"API request thread ending for answer index {answer_index}")
            self.is_streaming = False
            self.current_request_thread = None

    def _continue_after_tool_calls(
        self,
        answer_index: int,
        tool_results: list[str],
    ) -> None:
        """Automatically continue the conversation after tool calls are executed."""
        try:
            print(f"Starting continuation after tool calls for answer {answer_index}")

            # Add a brief pause to let tool results display
            time.sleep(0.3)

            # Check if we should stop
            if self.stop_streaming_flag.is_set():
                print("Continuation cancelled - stop flag set")
                return

            # Add a visual separator and continuation prompt
            continuation_separator = "\n\n---\n\nBased on the tool results above:\n\n"
            self.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=continuation_separator,
                    is_done=False,
                    is_error=False,
                ),
            )

            # Make the continuation request
            self._make_continuation_request(answer_index)

        except Exception as e:
            print(f"Error in continuation after tool calls: {e}")
            import traceback

            traceback.print_exc()

            # Mark as done if continuation setup fails
            self.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=f"\n\n[Error starting continuation: {str(e)}]",
                    is_done=True,
                    is_error=True,
                ),
            )

    def _make_continuation_request(self, answer_index: int) -> None:
        """Make a continuation request after tool calls have been executed."""
        try:
            print(f"Making continuation request for answer {answer_index}")

            # Check if we should stop before starting
            if self.stop_streaming_flag.is_set():
                print("Continuation cancelled - stop flag set")
                return

            # Get the current conversation context
            questions, answers, _ = self.chat_state.get_safe_copy()

            # Validate we have the expected data
            if answer_index >= len(questions) or answer_index >= len(answers):
                print(f"Invalid answer_index {answer_index} for continuation")
                self.content_update_queue.put(
                    ContentUpdate(
                        answer_index=answer_index,
                        content_chunk="",
                        is_done=True,
                        is_error=False,
                    ),
                )
                return

            # Prepare messages for the continuation
            messages: list[dict[str, str]] = []

            # Add all previous complete Q&A pairs (excluding current incomplete one)
            for i, (q, a) in enumerate(zip(questions, answers)):
                if i == answer_index:
                    # This is our current question - add it
                    messages.append({"role": "user", "content": expand(q)})

                    # Add the partial answer so far (including tool calls and results)
                    if a.strip():
                        messages.append({"role": "assistant", "content": a})
                    break
                elif q.strip() and a.strip():
                    # Complete Q&A pair from conversation history
                    messages.append({"role": "user", "content": expand(q)})
                    messages.append({"role": "assistant", "content": a})

            print(f"Prepared {len(messages)} messages for continuation")

            # Prepare the continuation request payload
            continuation_payload = {
                "model": self.parent.preferences["default_model"],
                "messages": messages,
                "stream": True,
            }

            # Add tools if available (in case the model wants to make more tool calls)
            available_tools = self.parent.get_available_mcp_tools()
            if available_tools:
                continuation_payload["tools"] = available_tools
                print(f"Added {len(available_tools)} tools to continuation request")

            # Make the continuation API request
            print("Starting continuation API request...")

            # Initialize content accumulator for tool call detection
            continuation_accumulated_content = ""

            with requests.post(
                BASE_URL,
                json=continuation_payload,
                stream=True,
                timeout=30,
            ) as response:

                if response.status_code != 200:
                    error_msg = (
                        f"Continuation API Error: Status code {response.status_code}"
                    )
                    print(f"{error_msg}: {response.text}")
                    self.content_update_queue.put(
                        ContentUpdate(
                            answer_index=answer_index,
                            content_chunk=f"\n\n[{error_msg}]",
                            is_done=True,
                            is_error=True,
                        ),
                    )
                    return

                print("Processing continuation response stream...")
                continuation_content_received = False

                for line in response.iter_lines(decode_unicode=True):
                    # Check for stop flag during continuation
                    if self.stop_streaming_flag.is_set():
                        print("Continuation stopped by user")
                        return

                    if not line:
                        continue

                    try:
                        data = json.loads(line.strip())

                        # Process message content
                        if "message" in data and "content" in data["message"]:
                            content_chunk = data["message"]["content"]
                            if content_chunk:
                                continuation_content_received = True
                                print(
                                    f"Continuation content: {repr(content_chunk[:50])}...",
                                )

                                # Accumulate content for tool call detection
                                continuation_accumulated_content += content_chunk

                                # Queue continuation content
                                self.content_update_queue.put(
                                    ContentUpdate(
                                        answer_index=answer_index,
                                        content_chunk=content_chunk,
                                        is_done=False,
                                        is_error=False,
                                    ),
                                )

                        # Check for completion
                        if data.get("done", False):
                            print("Continuation request completed")

                            # Check for tool calls in the continuation response
                            tool_calls = self._find_tool_calls(
                                continuation_accumulated_content,
                            )
                            if tool_calls:
                                print(
                                    f"Found {len(tool_calls)} tool call(s) in continuation response",
                                )

                                try:
                                    # Execute all tool calls found in continuation
                                    tool_results = self._execute_all_tool_calls(
                                        continuation_accumulated_content,
                                    )

                                    # Add all tool results to the response
                                    for i, tool_result in enumerate(tool_results):
                                        if tool_result:
                                            try:
                                                # Try to parse as JSON first
                                                data_result = json.loads(tool_result)
                                                if (
                                                    isinstance(data_result, dict)
                                                    and "content" in data_result
                                                ):
                                                    if (
                                                        isinstance(
                                                            data_result["content"],
                                                            list,
                                                        )
                                                        and len(data_result["content"])
                                                        > 0
                                                    ):
                                                        value = data_result["content"][
                                                            0
                                                        ].get("text", str(data_result))
                                                    else:
                                                        value = str(
                                                            data_result["content"],
                                                        )
                                                else:
                                                    value = json.dumps(
                                                        data_result,
                                                        indent=2,
                                                    )
                                            except (
                                                json.JSONDecodeError,
                                                KeyError,
                                                TypeError,
                                            ):
                                                # If not valid JSON or doesn't have expected structure, use as-is
                                                value = str(tool_result)

                                            result_text = f"\n\n**Continuation Tool {i + 1} Result:**\n```\n{value}\n```"

                                            # Queue the tool result
                                            self.content_update_queue.put(
                                                ContentUpdate(
                                                    answer_index=answer_index,
                                                    content_chunk=result_text,
                                                    is_done=False,
                                                    is_error=False,
                                                ),
                                            )

                                    print(
                                        f"All {len(tool_results)} continuation tool results added",
                                    )

                                    # Recursively continue after these tool calls
                                    self._continue_after_tool_calls(
                                        answer_index,
                                        tool_results,
                                    )
                                    return  # Don't mark as done yet, we're continuing again

                                except Exception as tool_error:
                                    print(
                                        f"Error executing continuation tool calls: {tool_error}",
                                    )
                                    import traceback

                                    traceback.print_exc()

                                    # Queue error message
                                    error_text = f"\n\n**Continuation Tool Execution Error:** {str(tool_error)}"
                                    self.content_update_queue.put(
                                        ContentUpdate(
                                            answer_index=answer_index,
                                            content_chunk=error_text,
                                            is_done=False,
                                            is_error=False,
                                        ),
                                    )

                            # If no continuation was received, add a fallback message
                            if not continuation_content_received:
                                fallback_msg = "\n\n[Tool execution completed]"
                                self.content_update_queue.put(
                                    ContentUpdate(
                                        answer_index=answer_index,
                                        content_chunk=fallback_msg,
                                        is_done=False,
                                        is_error=False,
                                    ),
                                )

                            # Mark as truly done (only if no tool calls were found)
                            self.content_update_queue.put(
                                ContentUpdate(
                                    answer_index=answer_index,
                                    content_chunk="",
                                    is_done=True,
                                    is_error=False,
                                ),
                            )
                            print(
                                f"Continuation fully completed for answer {answer_index}",
                            )
                            return

                    except json.JSONDecodeError as json_err:
                        print(f"JSON decode error in continuation: {json_err}")
                        continue
                    except Exception as content_err:
                        print(f"Error processing continuation content: {content_err}")
                        continue

                # If we exit the loop without getting 'done', still mark as complete
                print("Continuation stream ended without 'done' flag")
                self.content_update_queue.put(
                    ContentUpdate(
                        answer_index=answer_index,
                        content_chunk="",
                        is_done=True,
                        is_error=False,
                    ),
                )

        except requests.exceptions.Timeout:
            error_msg = "Continuation request timed out"
            print(error_msg)
            self.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=f"\n\n[{error_msg}]",
                    is_done=True,
                    is_error=True,
                ),
            )

        except requests.exceptions.ConnectionError:
            error_msg = "Continuation connection error - is Ollama running?"
            print(error_msg)
            self.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=f"\n\n[{error_msg}]",
                    is_done=True,
                    is_error=True,
                ),
            )

        except requests.exceptions.RequestException as req_err:
            error_msg = f"Continuation request error: {str(req_err)}"
            print(error_msg)
            self.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=f"\n\n[{error_msg}]",
                    is_done=True,
                    is_error=True,
                ),
            )

        except Exception as e:
            error_msg = f"Unexpected error in continuation: {str(e)}"
            print(f"{error_msg}")
            import traceback

            traceback.print_exc()

            self.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=f"\n\n[{error_msg}]",
                    is_done=True,
                    is_error=True,
                ),
            )

    def get_summary(self) -> None:
        # Call the API method directly - it already creates its own thread context
        summary_queue: queue.Queue = queue.Queue()
        threading.Thread(
            target=self.fetch_summary_response,
            args=(summary_queue,),
            daemon=True,
        ).start()

        # Handle the response in another thread to avoid blocking
        threading.Thread(
            target=self._handle_summary_response,
            args=(summary_queue,),
            daemon=True,
        ).start()

    def fetch_summary_response(self, summary_queue: queue.Queue) -> None:
        """Fetch a summary of the conversation with retry logic and proper error handling."""
        try:
            # Get current state from ChatState (thread-safe)
            questions, answers, _ = self.chat_state.get_safe_copy()

            # Add retry logic with delay
            max_retries = 3
            retry_delay = 1.0  # seconds

            for retry in range(max_retries):
                # Check if we have valid content to summarize
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
                    # Refresh the state
                    questions, answers, _ = self.chat_state.get_safe_copy()
                else:
                    print("No valid content available for summary after retries")
                    summary_queue.put("Chat Summary")
                    return

            # Create a summary prompt from the first question and answer
            first_q = questions[0]
            first_a = answers[0][:500]  # Limit answer length for summary

            summary_prompt = (
                f"Please provide a very brief summary (3-5 words) of this conversation (no period):\n\n"
                f"Q: {first_q}\nA: {first_a}"
            )

            messages = [{"role": "user", "content": summary_prompt}]

            payload = {
                "model": self.parent.preferences["summary_model"],
                "messages": messages,
                "stream": True,  # Enable streaming for summaries
            }

            print(
                f"Requesting summary with model: {self.parent.preferences['summary_model']}",
            )

            # Make the streaming API request with timeout
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

                # Process streaming response
                accumulated_summary = ""
                done_received = False

                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue

                    try:
                        data = json.loads(line.strip())

                        # Extract content from the response
                        if "message" in data and "content" in data["message"]:
                            content_chunk = data["message"]["content"]
                            if content_chunk:
                                accumulated_summary += content_chunk

                        # Check if response is complete
                        if data.get("done", False):
                            done_received = True
                            # Clean and limit summary length
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

                # Handle case where stream ends without done flag
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

    def _clean_summary(self, summary: str) -> str:
        """Clean and format the summary text."""
        # Remove newlines and extra whitespace
        cleaned = summary.strip().replace("\n", " ")

        # Remove any quotes that might be in the response
        cleaned = cleaned.replace('"', "").replace("'", "")

        # Limit length
        cleaned = cleaned[:50]

        # If empty after cleaning, return default
        if not cleaned:
            return "Chat Summary"

        # Capitalize first letter
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
