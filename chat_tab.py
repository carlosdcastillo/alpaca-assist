import json
import os
import platform
import queue
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple

import requests

from chat_state import ChatState
from expansion_language import expand
from syntax_text import SyntaxHighlightedText
from tooltip import ToolTip
from utils import ContentUpdate
from utils import is_macos


BASE_URL: str = "http://localhost:11434/api/chat"


class ChatTab:
    def __init__(
        self,
        parent: "ChatApp",
        notebook: ttk.Notebook,
        file_completions: List[str],
        preferences: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.chat_state = ChatState([], [])
        self.parent = parent
        self.notebook = notebook
        self.preferences = preferences or parent.preferences

        # Keep legacy lists for backward compatibility during transition
        self.chat_history_questions: List[str] = []
        self.chat_history_answers: List[str] = []

        self.input_queue: queue.Queue = queue.Queue()

        self.last_update_time: float = 0.0
        self.update_throttle = float(
            str(self.preferences.get("chat_update_throttle", 0.1)),
        )

        self.frame: ttk.Frame = ttk.Frame(notebook)
        notebook.add(self.frame, text=f"Tab {len(parent.tabs) + 1}")

        self.create_widgets()
        self.summary_generated: bool = False
        self.file_completions: List[str] = file_completions
        self.chat_display: SyntaxHighlightedText
        self.input_field: SyntaxHighlightedText

        # Add these new attributes for autocomplete tracking
        self.autocomplete_window: Optional[tk.Toplevel] = None
        self.autocomplete_listbox: Optional[tk.Listbox] = None
        self.file_trigger_position: Optional[str] = None
        self.filtered_completions: List[str] = []

        self.content_update_queue: queue.Queue[ContentUpdate] = queue.Queue()
        self.answer_end_positions: Dict[int, str] = {}  # Track where each answer ends

        # Thread safety for queue processor
        self._processor_lock = threading.Lock()
        self._queue_processor_running: bool = False

    def cleanup_resources(self):
        """Add this method - call when tab is destroyed"""
        # Stop queue processor
        with self._processor_lock:
            self._queue_processor_running = False

        # Clear queues to prevent memory leaks
        while not self.content_update_queue.empty():
            try:
                self.content_update_queue.get_nowait()
            except queue.Empty:
                break

        # Clean up autocomplete
        self.hide_autocomplete_menu()

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
                    else:
                        self.chat_state.append_to_answer(
                            update.answer_index,
                            update.content_chunk,
                        )
                        if update.is_done:
                            self.chat_state.finish_streaming()
                            streaming_finished = True
                        content_to_insert = update.content_chunk

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

    def has_pending_api_requests(self) -> bool:
        """Check if there are pending API requests."""
        return not self.input_queue.empty()

    def _finish_streaming(self):
        """Clean finish with single final highlight."""
        print("Finishing streaming and stopping processor")
        self._stop_processor()
        self.chat_display.config(state=tk.NORMAL)

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

    def create_widgets(self) -> None:
        chat_frame = ttk.Frame(self.frame)
        chat_frame.pack(expand=True, fill="both", padx=10, pady=10)

        # Create a PanedWindow for the splitter functionality
        self.paned_window = ttk.PanedWindow(chat_frame, orient=tk.VERTICAL)
        self.paned_window.pack(expand=True, fill="both")

        # Create frame for chat display
        chat_display_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(
            chat_display_frame,
            weight=3,
        )  # Give more weight to chat display

        self.chat_display = SyntaxHighlightedText(
            chat_display_frame,
            wrap=tk.WORD,
            height=20,
            theme_name=str(self.preferences["theme"]),
            background_color=str(self.preferences["background_color"]),
            font_family=str(self.preferences["font_family"]),
            font_size=int(str(self.preferences["font_size"])),
        )

        self.chat_display.pack(expand=True, fill="both")
        self.chat_display.bind("<Control-e>", self.go_to_end_of_line)
        self.chat_display.bind("<Control-a>", self.go_to_start_of_line)

        if is_macos():
            modifier = "Command"
        else:
            modifier = "Control"

        self.chat_display.bind(
            f"<{modifier}-t>",
            lambda e: self.parent.export_to_html() or "break",
        )

        self.chat_display.bind("<FocusIn>", self.parent.update_last_focused)
        self._last_chat_highlight_time = 0.0
        self.chat_display.bind("<KeyRelease>", self._handle_chat_display_key_release)

        # Create frame for input field
        input_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(input_frame, weight=1)  # Give less weight to input field

        input_field_frame = ttk.Frame(input_frame)
        input_field_frame.pack(fill="both", expand=True, side="left")

        self.input_field = SyntaxHighlightedText(
            input_field_frame,
            height=7,
            wrap=tk.WORD,
            theme_name=str(self.preferences["theme"]),
            background_color=str(self.preferences["background_color"]),
            font_family=str(self.preferences["font_family"]),
            font_size=int(str(self.preferences["font_size"])),
        )
        self.input_field.pack(side="left", expand=True, fill="both")
        self.input_field.bind(
            "<Control-Return>",
            lambda e: self.submit_message() or "break",
        )
        self.input_field.bind("<Control-e>", self.go_to_end_of_line)
        self.input_field.bind("<Control-a>", self.go_to_start_of_line)
        self.input_field.bind("<FocusIn>", self.parent.update_last_focused)
        self._last_input_highlight_time = 0.0
        self._input_highlight_throttle = 0.15  # 150ms throttle

        # Replace multiple bindings with single throttled handler
        self.input_field.bind("<KeyRelease>", self._handle_input_key_release)
        self.input_field.bind("<KeyPress>", self._handle_input_key_press)

        self.input_field.bind("<FocusOut>", lambda e: self.hide_autocomplete_menu())
        self.input_field.bind("<Button-1>", lambda e: self.hide_autocomplete_menu())

        button_frame = ttk.Frame(input_frame)
        button_frame.pack(side="left", padx=5, fill="y")

        if is_macos():
            # Use tk.Button for macOS
            submit_button = tk.Button(
                button_frame,
                text="Submit",
                command=self.submit_message,
                height=2,
            )
            submit_button.pack(pady=2, fill="x")
            ToolTip(submit_button, "Submit (Ctrl+Enter)")

            paste_button = tk.Button(
                button_frame,
                text="Paste",
                command=self.parent.paste_text,
                height=2,
            )
            paste_button.pack(pady=2, fill="x")
            ToolTip(paste_button, "Paste (Ctrl+V)")

            copy_button = tk.Button(
                button_frame,
                text="Copy",
                command=self.parent.copy_text,
                height=2,
            )
            copy_button.pack(pady=2, fill="x")
            ToolTip(copy_button, "Copy (Ctrl+C)")

            copy_code_button = tk.Button(
                button_frame,
                text="Copy Code",
                command=self.parent.copy_code_block,
                height=2,
            )
            copy_code_button.pack(pady=2, fill="x")
            ToolTip(copy_code_button, "Copy Code (Ctrl+B)")
        else:
            # Use ttk.Button for Windows and other platforms
            submit_button = ttk.Button(
                button_frame,
                text="Submit",
                command=self.submit_message,
                style="Custom.TButton",
            )
            submit_button.pack(pady=2, fill="x")
            ToolTip(submit_button, "Submit (Ctrl+Enter)")

            paste_button = ttk.Button(
                button_frame,
                text="Paste",
                command=self.parent.paste_text,
                style="Custom.TButton",
            )
            paste_button.pack(pady=2, fill="x")
            ToolTip(paste_button, "Paste (Ctrl+V)")

            copy_button = ttk.Button(
                button_frame,
                text="Copy",
                command=self.parent.copy_text,
                style="Custom.TButton",
            )
            copy_button.pack(pady=2, fill="x")
            ToolTip(copy_button, "Copy (Ctrl+C)")

            copy_code_button = ttk.Button(
                button_frame,
                text="Copy Code",
                command=self.parent.copy_code_block,
                style="Custom.TButton",
            )
            copy_code_button.pack(pady=2, fill="x")
            ToolTip(copy_code_button, "Copy Code (Ctrl+B)")

    def _handle_chat_display_key_release(self, event: tk.Event) -> None:
        current_time = time.time()

        if (
            current_time - self._last_chat_highlight_time
            >= self._input_highlight_throttle
        ):
            self.chat_display.highlight_text()
            self._last_chat_highlight_time = current_time

    def _handle_input_key_release(self, event: tk.Event) -> None:
        """Combined handler with throttling."""
        # Handle autocomplete
        self.check_for_autocomplete(event)

        # Throttled highlighting
        current_time = time.time()
        if (
            current_time - self._last_input_highlight_time
            >= self._input_highlight_throttle
        ):
            self.input_field.highlight_text()
            self._last_input_highlight_time = current_time

    def _handle_input_key_press(self, event: tk.Event) -> None:
        """Handle key press for autocomplete only."""
        self.check_for_autocomplete(event)

    def submit_message(self) -> str:
        """Submit message and initialize queue-based streaming."""
        message = self.input_field.get("1.0", tk.END)
        if message.strip():
            # Add to ChatState
            answer_index = self.chat_state.add_question(message.strip())

            # Add the question to the display immediately
            self.chat_display.config(state=tk.NORMAL)
            if answer_index > 0:
                # Add separator for subsequent questions
                sep = "-" * 80
                self.chat_display.insert(tk.END, f"\n\n{sep}\n\n")

            # Add Q: and A: lines
            self.chat_display.insert(tk.END, f"Q: {message.strip()}\n")
            self.chat_display.insert(tk.END, f"A:\n")

            # Track where this answer starts (at the end of the "A: " line)
            answer_start_pos = self.chat_display.index(tk.END + " -1c")
            self.answer_end_positions[answer_index] = answer_start_pos

            # Disable editing during streaming
            self.chat_display.config(state=tk.DISABLED)

            # Start the queue processor if needed (replaces the old atomic check)
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

            # Start API request thread
            threading.Thread(
                target=self.fetch_api_response,
                args=(answer_index,),
                daemon=True,
            ).start()

            # Clear input field
            self.input_field.delete("1.0", tk.END)

        return "break"

    def fetch_api_response(self, answer_index: int) -> None:
        """Fetch API response for a specific answer index using queue-based updates."""
        try:
            # Get the data payload for this request
            data_payload: Dict[str, Any] = self.input_queue.get(timeout=3)
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
            messages: List[Dict[str, str]] = []

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

            # Prepare Ollama API payload
            ollama_payload: Dict[str, Any] = {
                "model": data_payload["model"],
                "messages": messages,
                "stream": True,
            }

            print(f"Starting API request for answer index {answer_index}")

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
                    if not line:
                        continue

                    try:
                        data = json.loads(line.strip())

                        if "message" in data and "content" in data["message"]:
                            content_chunk = data["message"]["content"]
                            if content_chunk:
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
                            # Queue completion update
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
                        continue
                    except Exception as content_err:
                        print(f"Error processing content chunk: {content_err}")
                        continue

        except requests.exceptions.Timeout:
            error_msg = "Request timed out"
            print(f"API request timeout for answer index {answer_index}")
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
            self.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=error_msg,
                    is_done=True,
                    is_error=True,
                ),
            )

    def get_serializable_data(self) -> Dict[str, Any]:
        """Get data for serialization."""
        data = {
            "chat_state": self.chat_state.to_dict(),
            "summary_generated": self.summary_generated,
            # Keep legacy data for backward compatibility
            "chat_history_questions": self.chat_state.questions.copy(),
            "chat_history_answers": self.chat_state.answers.copy(),
        }

        # Add creation date if not already present
        if not hasattr(self, "created_date"):
            self.created_date = datetime.now().isoformat()
        data["created_date"] = self.created_date

        # Add original conversation ID if this was revived from history
        if hasattr(self, "original_conversation_id"):
            data["original_conversation_id"] = self.original_conversation_id

        return data

    def load_from_data(self, data: Dict[str, Any]) -> None:
        """Load data from serialized format."""
        # Try to load from new format first
        if "chat_state" in data:
            self.chat_state = ChatState.from_dict(data["chat_state"])
        else:
            # Fallback to legacy format
            questions = data.get("chat_history_questions", [])
            answers = data.get("chat_history_answers", [])
            self.chat_state = ChatState(questions, answers)

        # Update legacy lists for compatibility
        self.chat_history_questions = self.chat_state.questions.copy()
        self.chat_history_answers = self.chat_state.answers.copy()

        self.summary_generated = data.get("summary_generated", False)

        # Store the original conversation ID if this was revived
        if "original_conversation_id" in data:
            self.original_conversation_id = data["original_conversation_id"]

        # Store creation date
        if "created_date" in data:
            self.created_date = data["created_date"]

        # Generate summary for loaded sessions if not already generated and we have content
        if (
            not self.summary_generated
            and self.chat_state.questions
            and self.chat_state.answers
            and self.chat_state.answers[0].strip()
        ):
            # Delay summary generation to allow UI to settle
            self.parent.master.after(1000, lambda: self.get_summary())

    def rebuild_display_from_state(self):
        """Rebuild the entire display from ChatState (used for session loading)."""
        questions, answers, _ = self.chat_state.get_safe_copy()

        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)

        for i, (question, answer) in enumerate(zip(questions, answers)):
            self.chat_display.insert(tk.END, f"Q: {question}\n")
            self.chat_display.insert(tk.END, f"A: {answer}\n")

            if i < len(questions) - 1:
                sep = "-" * 80
                self.chat_display.insert(tk.END, f"\n{sep}\n\n")

        self.chat_display.highlight_text()

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

    def update_file_completions(self, new_completions: List[str]) -> None:
        self.file_completions = new_completions

    # Autocomplete methods
    def show_autocomplete_menu(self, filter_text: str = "") -> None:
        """Display the autocomplete listbox with file path completions."""
        # Filter completions
        if filter_text:
            self.filtered_completions = [
                comp
                for comp in self.file_completions
                if filter_text.lower() in os.path.basename(comp).lower()
            ]
        else:
            self.filtered_completions = self.file_completions.copy()

        if not self.filtered_completions:
            self.hide_autocomplete_menu()
            return

        # Hide existing menu
        self.hide_autocomplete_menu()

        # Get cursor position
        try:
            bbox_result = self.input_field.bbox("insert")
            if bbox_result is None:
                return
            x, y, _, h = bbox_result
        except tk.TclError:
            return

        # Create autocomplete window as child of main window (not input field)
        self.autocomplete_window = tk.Toplevel(self.parent.master)
        self.autocomplete_window.wm_overrideredirect(True)

        # macOS-specific configuration
        is_macos_system = self.parent.master.tk.call("tk", "windowingsystem") == "aqua"

        if is_macos_system:
            self.autocomplete_window.configure(
                bg="#FFFFFF",
                relief="solid",
                bd=1,
                highlightthickness=0,
            )
            self.autocomplete_window.attributes("-topmost", True)
        else:
            self.autocomplete_window.configure(bg="white", relief="solid", bd=1)

        # Position window relative to input field but as child of main window
        try:
            # Get absolute position of cursor in input field
            input_abs_x = self.input_field.winfo_rootx() + x
            input_abs_y = self.input_field.winfo_rooty() + y + h + 2

            # Position relative to main window
            self.autocomplete_window.geometry(f"+{input_abs_x}+{input_abs_y}")

            if is_macos_system:
                self.autocomplete_window.update_idletasks()
                self.autocomplete_window.lift()

        except tk.TclError as e:
            print(f"Error positioning autocomplete window: {e}")
            self.hide_autocomplete_menu()
            return

        # Rest of the method remains the same...
        font_family = str(self.preferences["font_family"])
        font_size = int(str(self.preferences["font_size"]))

        listbox_config = {
            "height": min(8, len(self.filtered_completions)),
            "width": 50,
            "font": (font_family, font_size),
            "exportselection": False,
            "activestyle": "none",
        }

        if is_macos_system:
            listbox_config.update(
                {
                    "bg": "#FFFFFF",
                    "fg": "#000000",
                    "selectbackground": "#007AFF",
                    "selectforeground": "#FFFFFF",
                    "highlightthickness": 0,
                    "borderwidth": 0,
                    "relief": "flat",
                },
            )
        else:
            listbox_config.update(
                {
                    "bg": "white",
                    "fg": "black",
                    "selectbackground": "#0078d4",
                    "selectforeground": "white",
                },
            )

        self.autocomplete_listbox = tk.Listbox(
            self.autocomplete_window,
            **listbox_config,
        )
        self.autocomplete_listbox.pack(padx=2, pady=2, fill="both", expand=True)

        # Populate listbox
        for completion in self.filtered_completions:
            display_name = os.path.basename(completion)
            self.autocomplete_listbox.insert(tk.END, display_name)

        # Select first item
        if self.filtered_completions:
            self.autocomplete_listbox.selection_set(0)
            self.autocomplete_listbox.activate(0)

        # Bind events
        self.autocomplete_listbox.bind("<Double-Button-1>", self.on_autocomplete_select)
        self.autocomplete_listbox.bind("<Return>", self.on_autocomplete_select)

        if is_macos_system:
            self.autocomplete_window.update_idletasks()
            self.autocomplete_listbox.update_idletasks()
            self.autocomplete_window.lift()
            self.autocomplete_window.attributes("-topmost", True)

            self.parent.master.after(1, self._force_listbox_redraw)
            self.parent.master.after(10, self._force_listbox_redraw)
            self.parent.master.after(50, self._force_listbox_redraw)
        else:
            self.autocomplete_window.update_idletasks()

    def _force_listbox_redraw(self) -> None:
        """Force the listbox to redraw - macOS workaround."""
        if self.autocomplete_listbox and self.autocomplete_window:
            try:
                # Multiple techniques to force redraw
                self.autocomplete_listbox.update_idletasks()
                self.autocomplete_listbox.update()

                # Force a selection refresh
                current_selection = self.autocomplete_listbox.curselection()
                if current_selection:
                    idx = current_selection[0]
                    self.autocomplete_listbox.selection_clear(0, tk.END)
                    self.autocomplete_listbox.selection_set(idx)
                    self.autocomplete_listbox.activate(idx)
                    self.autocomplete_listbox.see(idx)

                # Force window refresh
                self.autocomplete_window.update_idletasks()

            except tk.TclError:
                # Widget might be destroyed
                pass

    def hide_autocomplete_menu(self) -> None:
        """Hide the autocomplete window if it's currently shown."""
        if self.autocomplete_window:
            try:
                self.autocomplete_window.destroy()
            except tk.TclError:
                pass
            self.autocomplete_window = None
            self.autocomplete_listbox = None

    def on_autocomplete_select(self, event: tk.Event) -> None:
        """Handle selection from the autocomplete listbox."""
        if self.autocomplete_listbox:
            selection = self.autocomplete_listbox.curselection()
            if selection:
                index = selection[0]
                if 0 <= index < len(self.filtered_completions):
                    selected_file = self.filtered_completions[index]
                    self.insert_completion(selected_file)

    def insert_completion(self, option: str) -> None:
        """Insert the selected completion, replacing any partially typed text."""
        if self.file_trigger_position:
            # Delete from the trigger position to current cursor
            self.input_field.delete(self.file_trigger_position, tk.INSERT)
            # Insert the completion
            self.input_field.insert(self.file_trigger_position, option + " ")
        else:
            # Fallback: just insert at cursor
            cursor_position = self.input_field.index(tk.INSERT)
            self.input_field.insert(cursor_position, option + " ")

        self.hide_autocomplete_menu()
        self.file_trigger_position = None
        self.input_field.focus_set()

    def check_for_autocomplete(self, event: tk.Event) -> None:
        """Check if we should show autocomplete and filter based on typed text."""
        # Handle special keys for autocomplete navigation - only on KeyPress to avoid double-triggering
        if (
            self.autocomplete_window
            and self.autocomplete_listbox
            and event.type == tk.EventType.KeyPress
        ):
            if event.keysym == "Down":
                current = self.autocomplete_listbox.curselection()
                if current:
                    next_index = min(
                        current[0] + 1,
                        self.autocomplete_listbox.size() - 1,
                    )
                else:
                    next_index = 0
                self.autocomplete_listbox.selection_clear(0, tk.END)
                self.autocomplete_listbox.selection_set(next_index)
                self.autocomplete_listbox.activate(next_index)
                self.autocomplete_listbox.see(next_index)
                return
            elif event.keysym == "Up":
                current = self.autocomplete_listbox.curselection()
                if current:
                    prev_index = max(current[0] - 1, 0)
                else:
                    prev_index = 0
                self.autocomplete_listbox.selection_clear(0, tk.END)
                self.autocomplete_listbox.selection_set(prev_index)
                self.autocomplete_listbox.activate(prev_index)
                self.autocomplete_listbox.see(prev_index)
                return
            elif event.keysym == "Return":
                selection = self.autocomplete_listbox.curselection()
                if selection:
                    index = selection[0]
                    if 0 <= index < len(self.filtered_completions):
                        selected_file = self.filtered_completions[index]
                        self.insert_completion(selected_file)
                return
            elif event.keysym == "Escape":
                self.hide_autocomplete_menu()
                return

        # Only handle text input changes on KeyRelease to avoid issues with special keys
        # Skip navigation keys to prevent interference
        if event.type == tk.EventType.KeyRelease and event.keysym not in [
            "Down",
            "Up",
            "Return",
            "Escape",
            "Left",
            "Right",
        ]:

            current_line = self.input_field.get("insert linestart", "insert")

            # Check if we just typed the trigger sequence
            if event.char in [":", "/"]:
                if current_line.endswith("/file:") or current_line.endswith("/file"):
                    # Store the position where file completion started
                    self.file_trigger_position = self.input_field.index("insert")
                    self.show_autocomplete_menu()
                    return

            # Check if we're currently in a file completion context
            file_match = re.search(r"/file:([^/\s]*)", current_line)
            if file_match:
                # We're typing after /file:
                typed_text = file_match.group(1)
                # Update trigger position to be right after the colon
                colon_pos = current_line.rfind("/file:") + 6  # 6 = len('/file:')
                self.file_trigger_position = f"insert linestart + {colon_pos}c"
                self.show_autocomplete_menu(typed_text)
            else:
                # We're not in file completion context, hide menu
                self.hide_autocomplete_menu()
                self.file_trigger_position = None

    def go_to_end_of_line(self, event: tk.Event) -> str:
        widget = event.widget
        widget.mark_set(tk.INSERT, "insert lineend")
        return "break"  # This prevents the default behavior

    def go_to_start_of_line(self, event: tk.Event) -> str:
        widget = event.widget
        widget.mark_set(tk.INSERT, "insert linestart")
        return "break"  # This prevents the default behavior
