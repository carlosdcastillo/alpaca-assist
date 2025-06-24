import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
from typing import Any
from typing import cast
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Union

import pyperclip  # type: ignore
import requests  # type: ignore

from chat_state import ChatState
from expansion_language import expand
from preferences import DEFAULT_PREFERENCES
from preferences import PreferencesWindow
from syntax_text import SyntaxHighlightedText
from text_utils import parse_code_blocks
from tooltip import ToolTip

BASE_URL: str = "http://localhost:11434/api/chat"


class ContentUpdate(NamedTuple):
    answer_index: int
    content_chunk: str
    is_done: bool = False
    is_error: bool = False


class ChatTab:
    def __init__(
        self,
        parent: "ChatApp",
        notebook: ttk.Notebook,
        file_completions: List[str],
        preferences: Optional[dict[str, Any]] = None,
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
        self.answer_end_positions: dict[int, str] = {}  # Track where each answer ends

        self._queue_processor_running: bool = False

    def process_content_queue(self) -> None:
        """Process queue with proper lifecycle management."""
        updates_processed = 0
        streaming_finished = False

        # Process all available updates
        while True:
            try:
                update = self.content_update_queue.get_nowait()

                # Update ChatState (for data integrity)
                if update.is_error:
                    error_content = f"\n\n[Error: {update.content_chunk}]"
                    self.chat_state.append_to_answer(update.answer_index, error_content)
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
                self._insert_content_at_answer(update.answer_index, content_to_insert)
                updates_processed += 1

                # Handle summary generation on completion
                if (
                    update.is_done
                    and update.answer_index == 0
                    and not self.summary_generated
                ):
                    self.summary_generated = True
                    self.parent.master.after(500, self.get_summary)

            except queue.Empty:
                break  # No more updates to process right now

        # Highlight syntax after all updates (more efficient than per-chunk)
        if updates_processed > 0:
            self.chat_display.highlight_text()

        # Continue processing unless streaming is finished
        if not streaming_finished:
            # Schedule next check - keep the processor running
            self.parent.master.after(50, self.process_content_queue)
        else:
            # Stop the processor only when streaming is actually complete
            self._queue_processor_running = False
            print(f"Queue processor stopped. Processed {updates_processed} updates.")

            # Re-enable text widget editing when streaming is complete
            self.chat_display.config(state=tk.NORMAL)

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
        self.chat_display.bind("<FocusIn>", self.parent.update_last_focused)
        self.chat_display.bind(
            "<KeyRelease>",
            lambda e: self.chat_display.highlight_text(),
        )

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
        self.input_field.bind("<KeyRelease>", self.check_for_autocomplete)
        self.input_field.bind(
            "<KeyRelease>",
            lambda e: self.input_field.highlight_text(),
            add=True,
        )
        self.input_field.bind("<KeyPress>", self.check_for_autocomplete)
        self.input_field.bind(
            "<KeyPress>",
            lambda e: self.input_field.highlight_text(),
            add=True,
        )
        self.input_field.bind("<FocusOut>", lambda e: self.hide_autocomplete_menu())
        self.input_field.bind("<Button-1>", lambda e: self.hide_autocomplete_menu())

        button_frame = ttk.Frame(input_frame)
        button_frame.pack(side="left", padx=5, fill="y")

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

            # Start the queue processor if not already running
            if not self._queue_processor_running:
                self._queue_processor_running = True
                self.process_content_queue()

            # Update legacy lists for compatibility
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

            threading.Thread(
                target=self.parent.fetch_api_response,
                args=(self, answer_index),
                daemon=True,
            ).start()

            self.input_field.delete("1.0", tk.END)

        return "break"

    def get_serializable_data(self) -> dict[str, Any]:
        """Get data for serialization."""
        return {
            "chat_state": self.chat_state.to_dict(),
            "summary_generated": self.summary_generated,
            # Keep legacy data for backward compatibility
            "chat_history_questions": self.chat_state.questions.copy(),
            "chat_history_answers": self.chat_state.answers.copy(),
        }

    def load_from_data(self, data: dict[str, Any]) -> None:
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

        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.highlight_text()

    def _save_scroll_position(self) -> dict:
        """Save scroll position with fractional position and content verification."""
        scroll_info = {
            "was_at_bottom": False,
            "fractional_position": 0.0,
            "verification_content": "",
        }

        try:
            # Get current scroll position
            current_scroll = self.chat_display.yview()
            scroll_info["fractional_position"] = current_scroll[0]

            # Check if user is at the very bottom
            scroll_info["was_at_bottom"] = current_scroll[1] >= 0.999

            # Get content around the top visible area for verification
            try:
                top_visible_index = self.chat_display.index("@0,0")

                # Get 50 characters starting from the top visible position for verification
                end_index = f"{top_visible_index} + 50c"
                verification_content = self.chat_display.get(
                    top_visible_index,
                    end_index,
                )

                # Clean up the content (remove newlines for easier matching)
                scroll_info["verification_content"] = verification_content.replace(
                    "\n",
                    " ",
                ).strip()

            except (tk.TclError, ValueError):
                pass

        except Exception as e:
            print(f"Error saving scroll position: {e}")

        return scroll_info

    def _restore_scroll_position(self, scroll_info: dict) -> None:
        """Restore scroll position using fractional position and verify with content."""
        try:
            # If user was at bottom, scroll to bottom
            if scroll_info.get("was_at_bottom", False):
                self.chat_display.yview_moveto(1.0)
                return

            # First, go to the saved fractional position
            fractional_pos = scroll_info.get("fractional_position", 0.0)
            if 0.0 <= fractional_pos <= 1.0:
                self.chat_display.yview_moveto(fractional_pos)

            # Now verify we're at the right place by checking content
            verification_content = scroll_info.get("verification_content", "")
            if verification_content:
                # Get content at current top visible position
                try:
                    current_top = self.chat_display.index("@0,0")
                    end_index = f"{current_top} + 50c"
                    current_content = self.chat_display.get(current_top, end_index)
                    current_content = current_content.replace("\n", " ").strip()

                    # If content matches, we're good
                    if current_content == verification_content:
                        return

                    # Content doesn't match, try to find it nearby
                    self._fine_tune_scroll_position(
                        verification_content,
                        fractional_pos,
                    )

                except (tk.TclError, ValueError):
                    pass

        except Exception as e:
            print(f"Error restoring scroll position: {e}")

    def _fine_tune_scroll_position(
        self,
        target_content: str,
        base_fractional_pos: float,
    ) -> None:
        """Fine-tune scroll position by searching around the fractional position."""
        try:
            # Search in a small range around the base fractional position
            search_range = 0.05  # Search within 5% of the document
            min_pos = max(0.0, base_fractional_pos - search_range)
            max_pos = min(1.0, base_fractional_pos + search_range)

            # Try positions in small increments around the base position
            step = 0.001  # 0.1% increments
            best_match_pos = base_fractional_pos
            best_match_score = 0

            current_pos = min_pos
            while current_pos <= max_pos:
                # Move to this position
                self.chat_display.yview_moveto(current_pos)

                try:
                    # Get content at this position
                    top_index = self.chat_display.index("@0,0")
                    end_index = f"{top_index} + 50c"
                    content = self.chat_display.get(top_index, end_index)
                    content = content.replace("\n", " ").strip()

                    # Calculate similarity score (simple approach)
                    match_score = self._calculate_content_similarity(
                        content,
                        target_content,
                    )

                    if match_score > best_match_score:
                        best_match_score = match_score
                        best_match_pos = current_pos

                    # If we found an exact match, stop searching
                    if match_score >= 0.9:  # 90% similarity threshold
                        break

                except (tk.TclError, ValueError):
                    pass

                current_pos += step

            # Move to the best position found
            self.chat_display.yview_moveto(best_match_pos)

        except Exception as e:
            print(f"Error fine-tuning scroll position: {e}")

    def _calculate_content_similarity(self, content1: str, content2: str) -> float:
        """Calculate similarity between two content strings."""
        if not content1 or not content2:
            return 0.0

        if content1 == content2:
            return 1.0

        # Simple similarity based on common words
        words1 = set(content1.lower().split())
        words2 = set(content2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = words1.intersection(words2)
        union = words1.union(words2)

        return len(intersection) / len(union) if union else 0.0

    def _do_update(self):
        """Perform the actual display update."""
        if hasattr(self, "pending_update_id"):
            delattr(self, "pending_update_id")

        # Get current state from ChatState
        questions, answers, _ = self.chat_state.get_safe_copy()

        # Save scroll position
        scroll_info = self._save_scroll_position()

        # Update display
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)

        for i, (question, answer) in enumerate(zip(questions, answers)):
            self.chat_display.insert(tk.END, f"Q: {question}\n")
            self.chat_display.insert(tk.END, f"A: {answer}\n")

            if i < len(questions) - 1:
                sep = "-" * 80
                self.chat_display.insert(tk.END, f"\n{sep}\n\n")

        # self.chat_display.config(state=tk.DISABLED)

        # Force full re-highlighting after rebuild
        self.chat_display.last_highlighted_content = ""
        self.chat_display.last_highlighted_length = 0
        # Restore scroll position
        self._restore_scroll_position(scroll_info)

        # Highlight after a brief delay
        self.parent.master.after(10, self.chat_display.highlight_text)

    def get_summary(self) -> None:
        # Call the API method directly - it already creates its own thread context
        summary_queue: queue.Queue = queue.Queue()
        threading.Thread(
            target=self.parent.fetch_api_response_summary,
            args=(self, summary_queue),
            daemon=True,
        ).start()

        # Handle the response in another thread to avoid blocking
        threading.Thread(
            target=self._handle_summary_response,
            args=(summary_queue,),
            daemon=True,
        ).start()

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

    def update_file_completions(
        self,
        new_completions: List[str],
    ) -> None:  # Renamed from update_file_options
        self.file_completions = new_completions

    def show_autocomplete_menu(self, filter_text: str = "") -> None:
        """
        Display the autocomplete listbox with file path completions filtered by the typed text.
        """
        # Filter completions based on the typed text
        if filter_text:
            self.filtered_completions = [
                comp
                for comp in self.file_completions
                if filter_text.lower() in os.path.basename(comp).lower()
            ]
        else:
            self.filtered_completions = self.file_completions.copy()

        # Don't show menu if no matches
        if not self.filtered_completions:
            self.hide_autocomplete_menu()
            return

        # Hide existing menu if any
        self.hide_autocomplete_menu()

        # Get the current cursor position
        bbox_result = self.input_field.bbox("insert")
        if bbox_result is None:
            return

        x, y, _, h = bbox_result

        # Create autocomplete window
        self.autocomplete_window = tk.Toplevel(self.input_field)
        self.autocomplete_window.wm_overrideredirect(True)
        self.autocomplete_window.configure(bg="white", relief="solid", bd=1)

        # Position the window below the cursor
        window_x = self.input_field.winfo_rootx() + x
        window_y = self.input_field.winfo_rooty() + y + h
        self.autocomplete_window.geometry(f"+{window_x}+{window_y}")

        # Create listbox
        self.autocomplete_listbox = tk.Listbox(
            self.autocomplete_window,
            height=min(8, len(self.filtered_completions)),
            width=50,
            font=("Cascadia Mono", 10),
            bg="white",
            fg="black",
            selectbackground="#0078d4",
            selectforeground="white",
            activestyle="none",
        )
        self.autocomplete_listbox.pack()

        # Populate listbox
        for completion in self.filtered_completions:
            display_name = os.path.basename(completion)
            self.autocomplete_listbox.insert(tk.END, display_name)

        # Select first item by default
        if self.filtered_completions:
            self.autocomplete_listbox.selection_set(0)
            self.autocomplete_listbox.activate(0)

        # Bind events for listbox interaction
        self.autocomplete_listbox.bind("<Double-Button-1>", self.on_autocomplete_select)
        self.autocomplete_listbox.bind("<Return>", self.on_autocomplete_select)

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


class ChatApp:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        master.title("Alpaca Assist")

        # Initialize and load preferences FIRST
        self.preferences = DEFAULT_PREFERENCES.copy()
        self.load_preferences()

        # Set window geometry from preferences
        master.geometry(str(self.preferences["window_geometry"]))

        # Initialize other attributes
        self.style = ttk.Style()
        self.style.configure("Custom.TButton", padding=(10, 10), width=15)
        self.style.configure("TNotebook.Tab", padding=(4, 4))

        self.file_completions: List[str] = []
        self.last_focused_widget: Optional[SyntaxHighlightedText] = None
        self.tabs: List[ChatTab] = []
        self.load_file_completions()

        # Create widgets AFTER preferences are loaded
        self.create_widgets()
        self.create_menu()
        self.bind_shortcuts()

        # No need for apply_preferences() here since widgets are created with correct settings

        # Set up protocol for when window is closed
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Load saved session
        self.load_session()

    def on_tab_changed(self, event: tk.Event) -> None:
        """Handle tab selection changes and update window title."""
        try:
            selected_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= selected_tab_index < len(self.tabs):
                tab_name = self.notebook.tab(selected_tab_index, "text")
                self.master.title(f"Alpaca Assist - {tab_name}")
        except tk.TclError:
            # Handle case where no tab is selected
            self.master.title("Alpaca Assist")

    def load_preferences(self) -> None:
        """Load preferences from file."""
        if os.path.exists("preferences.json"):
            try:
                with open("preferences.json", "r") as f:
                    saved_prefs = json.load(f)
                    self.preferences.update(saved_prefs)
            except Exception as e:
                print(f"Error loading preferences: {e}")

    def save_preferences(self) -> None:
        """Save preferences to file."""
        try:
            with open("preferences.json", "w") as f:
                json.dump(self.preferences, f, indent=2)
        except Exception as e:
            print(f"Error saving preferences: {e}")

    def apply_preferences(self) -> None:
        """Apply all preferences to the application."""
        self.apply_appearance_preferences(self.preferences)

        # Update global BASE_URL
        global BASE_URL
        BASE_URL = str(
            self.preferences.get("api_url", "http://localhost:11434/api/chat"),
        )

    def apply_appearance_preferences(self, prefs: dict[str, Any]) -> None:
        """Apply appearance-related preferences."""

        # Update all text widgets with new font, theme, and background
        for i, tab in enumerate(self.tabs):

            # Apply in specific order: font first, then background, then theme
            tab.chat_display.update_font(prefs["font_family"], prefs["font_size"])
            tab.input_field.update_font(prefs["font_family"], prefs["font_size"])

            tab.chat_display.update_background_color(prefs["background_color"])
            tab.input_field.update_background_color(prefs["background_color"])

            # Apply theme last so it can override background-specific colors
            tab.chat_display.update_theme(prefs["theme"])
            tab.input_field.update_theme(prefs["theme"])

            # Update undo settings
            tab.chat_display.config(maxundo=prefs["max_undo_levels"])
            tab.input_field.config(maxundo=prefs["max_undo_levels"])

    def show_preferences(self) -> None:
        """Show the preferences window."""
        PreferencesWindow(self)

    def undo_text(self) -> None:
        """Undo text in the currently focused widget."""
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            self.last_focused_widget.undo(None)

    def on_closing(self) -> None:
        """Handle application closing by saving session and quitting."""
        # Save current window geometry
        self.preferences["window_geometry"] = self.master.geometry()
        self.save_preferences()

        if self.preferences["auto_save"]:
            self.save_session()
        self.master.destroy()

    def create_widgets(self) -> None:
        # Create button frame
        self.button_frame = ttk.Frame(self.master)
        self.button_frame.pack(fill="x", padx=5, pady=(4, 2))  # Add top padding

        # Create a custom style for medium-height buttons
        self.style.configure(
            "Medium.TButton",
            padding=(9, 11),
        )  # Adjust vertical padding to a medium value

        # Create New Tab button with medium-height style
        self.new_tab_button = ttk.Button(
            self.button_frame,
            text="New Tab",
            command=self.create_tab,
            style="Medium.TButton",  # Apply medium-height style
        )
        self.new_tab_button.pack(side="left", padx=(3, 1))
        ToolTip(self.new_tab_button, "New Tab (Ctrl+N)")

        # Create Delete Tab button with medium-height style
        self.delete_tab_button = ttk.Button(
            self.button_frame,
            text="Delete Tab",
            command=self.delete_tab,
            style="Medium.TButton",  # Apply medium-height style
        )
        self.delete_tab_button.pack(side="left", padx=(1, 3))
        ToolTip(self.delete_tab_button, "Delete Tab (Ctrl+W)")

        # Create notebook
        self.notebook = ttk.Notebook(self.master)
        self.notebook.pack(
            expand=True,
            fill="both",
            padx=10,
            pady=(5, 10),
        )  # Adjust top padding

        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        # Add focus event handlers
        self.master.bind("<FocusIn>", self.on_app_focus_in)
        self.master.bind("<FocusOut>", self.on_app_focus_out)

        # Initialize update flag
        self.update_enabled = True

    def save_session(self) -> None:
        """Save all tabs and their contents to disk."""
        # Get the currently selected tab index
        current_tab_index = (
            self.notebook.index(self.notebook.select()) if self.tabs else 0
        )

        session_data: dict[str, Any] = {
            "tabs": [],
            "window": {
                "geometry": self.master.geometry(),
            },
            "selected_tab_index": current_tab_index,
            "version": "1.1",  # Add version for future compatibility
        }

        for tab in self.tabs:
            tab_data: dict[str, Any] = {
                "name": self.notebook.tab(self.tabs.index(tab), "text"),
                **tab.get_serializable_data(),  # Use the new serialization method
            }
            session_data["tabs"].append(tab_data)

        try:
            with open("chat_session.json", "w") as f:
                json.dump(session_data, f, indent=2)
            print("Session saved successfully")
        except Exception as e:
            print(f"Error saving session: {e}")

    def load_session(self) -> None:
        """Load saved tabs and their contents from disk."""
        if not os.path.exists("chat_session.json"):
            self.create_tab()
            return

        try:
            with open("chat_session.json", "r") as f:
                session_data: dict[str, Any] = json.load(f)

            # Restore window geometry if available
            if "window" in session_data and "geometry" in session_data["window"]:
                self.master.geometry(
                    cast(dict[str, str], session_data["window"])["geometry"],
                )

            # Clear any default tabs
            if self.tabs:
                for tab in self.tabs:
                    self.notebook.forget(tab.frame)
                self.tabs = []

            for tab_data in cast(list[dict[str, Any]], session_data.get("tabs", [])):
                new_tab: ChatTab = ChatTab(self, self.notebook, self.file_completions)

                # Use the new loading method
                new_tab.load_from_data(tab_data)

                # Add the tab to the list
                self.tabs.append(new_tab)

                # Update the tab's display
                new_tab.rebuild_display_from_state()

                # Set the tab name
                tab_name: str = tab_data.get("name", f"Tab {len(self.tabs)}")
                self.notebook.tab(self.tabs.index(new_tab), text=tab_name)

            # If no tabs were loaded, create a default one
            if not self.tabs:
                self.create_tab()

            # Select the previously selected tab if available
            selected_tab_index = session_data.get("selected_tab_index", 0)
            if 0 <= selected_tab_index < len(self.tabs):
                self.notebook.select(selected_tab_index)
                # Update window title with selected tab name
                tab_name = self.notebook.tab(selected_tab_index, "text")
                self.master.title(f"Alpaca Assist - {tab_name}")

            print("Session loaded successfully")
        except Exception as e:
            print(f"Error loading session: {e}")
            self.create_tab()

    def bind_shortcuts(self) -> None:
        self.master.bind("<Control-n>", lambda e: self.create_tab())
        self.master.bind("<Control-w>", lambda e: self.delete_tab())
        # Fix the Ctrl+Enter binding to return "break" to prevent default behavior
        self.master.bind("<Control-c>", lambda e: self.copy_text())
        self.master.bind("<Control-v>", self.paste_text())
        self.master.bind("<Control-b>", lambda e: self.copy_code_block())
        self.master.bind("<Control-m>", lambda e: self.manage_file_completions())
        self.master.bind("<Control-e>", self.go_to_end_of_line)
        self.master.bind("<Control-a>", self.go_to_start_of_line)
        self.master.bind("<Control-z>", lambda e: self.undo_text())
        self.master.bind("<Control-comma>", lambda e: self.show_preferences())

    def on_app_focus_in(self, event: tk.Event) -> None:
        """Re-enable UI updates when app gains focus"""
        self.update_enabled = True

    def on_app_focus_out(self, event: tk.Event) -> None:
        """Reduce UI updates when app loses focus"""
        self.update_enabled = False

    def create_menu(self) -> None:
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="New Tab",
            command=self.create_tab,
            accelerator="Ctrl+N",
        )
        file_menu.add_command(
            label="Close Tab",
            command=self.delete_tab,
            accelerator="Ctrl+W",
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Preferences...",
            command=self.show_preferences,
            accelerator="Ctrl+,",
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.master.quit)

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(
            label="Undo",
            command=self.undo_text,
            accelerator="Ctrl+Z",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Copy",
            command=self.copy_text,
            accelerator="Ctrl+C",
        )
        edit_menu.add_command(
            label="Paste",
            command=self.paste_text,
            accelerator="Ctrl+V",
        )
        edit_menu.add_command(
            label="Copy Code Block",
            command=self.copy_code_block,
            accelerator="Ctrl+B",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Manage File Completions",
            command=self.manage_file_completions,
            accelerator="Ctrl+M",
        )
        # Chat menu
        chat_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Chat", menu=chat_menu)
        chat_menu.add_command(
            label="Submit Message",
            command=self.submit_current_tab,
            accelerator="Ctrl+Enter",
        )

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def manage_file_completions(self) -> None:  # Renamed from manage_file_options
        completions_window = tk.Toplevel(self.master)
        completions_window.title("Manage File Completions")
        completions_window.geometry("400x300")

        listbox = tk.Listbox(completions_window, width=50, selectmode=tk.MULTIPLE)
        listbox.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        for completion in self.file_completions:
            listbox.insert(tk.END, completion)

        def add_completions() -> None:
            new_completions = filedialog.askopenfilenames(
                title="Select files",
                filetypes=[("All files", "*.*")],
                parent=completions_window,
            )
            if new_completions:
                for completion in new_completions:
                    if completion not in self.file_completions:
                        self.file_completions.append(completion)
                        listbox.insert(tk.END, completion)

        def remove_completions() -> None:
            selected = listbox.curselection()
            if selected:
                for index in reversed(selected):
                    listbox.delete(index)
                    del self.file_completions[index]

        button_frame = ttk.Frame(completions_window)
        button_frame.pack(pady=5)

        add_button = ttk.Button(button_frame, text="Add Files", command=add_completions)
        add_button.pack(side=tk.LEFT, padx=5)

        remove_button = ttk.Button(
            button_frame,
            text="Remove Selected",
            command=remove_completions,
        )
        remove_button.pack(side=tk.LEFT)

        def on_closing() -> None:
            self.update_tabs_file_completions()
            self.save_file_completions()  # Save file completions when window closes
            completions_window.destroy()

        completions_window.protocol("WM_DELETE_WINDOW", on_closing)

    def submit_current_tab(self) -> str:
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            current_tab.submit_message()
        return "break"  # Return "break" to prevent default behavior

    def show_about(self) -> None:
        about_text = (
            "Alpaca Assist\n\nVersion 0.03\n\nA chat application using the Ollama API."
        )
        tk.messagebox.showinfo("About", about_text)

    def save_file_completions(self) -> None:
        with open("file_completions.json", "w") as f:
            json.dump(self.file_completions, f, indent=2)

    def load_file_completions(self) -> None:
        if os.path.exists("file_completions.json"):
            with open("file_completions.json", "r") as f:
                self.file_completions = json.load(f)

    def update_tabs_file_completions(self) -> None:
        for tab in self.tabs:
            tab.update_file_completions(self.file_completions)
        self.save_file_completions()  # Save file completions after updating

    def create_tab(self, tab_name: Optional[str] = None) -> None:
        """Create a new tab."""
        tab = ChatTab(self, self.notebook, self.file_completions, self.preferences)
        self.tabs.append(tab)
        if tab_name is None:
            tab_name = f"Chat {len(self.tabs)}"
        self.notebook.add(tab.frame, text=tab_name)
        self.notebook.select(len(self.tabs) - 1)

        # Update window title with the new tab name
        self.master.title(f"Alpaca Assist - {tab_name}")

    def handle_ctrl_return(self, tab) -> str:
        """Handle Ctrl+Return event for a specific tab."""
        tab.submit_message()
        return "break"

    def delete_tab(self) -> None:
        if len(self.tabs) > 1:
            current_tab = self.notebook.select()
            tab_index = self.notebook.index(current_tab)
            self.notebook.forget(current_tab)
            del self.tabs[tab_index]

    def update_last_focused(self, event: tk.Event) -> None:
        self.last_focused_widget = cast(SyntaxHighlightedText, event.widget)

    def paste_text(self) -> str:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            text = pyperclip.paste()

            # Delete any selected text first
            try:
                self.last_focused_widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                # No selection, continue
                pass

            # Process the text to ensure proper line handling
            # Replace all newlines with tkinter's internal newline representation
            processed_text = text.replace("\r\n", "\n").replace("\r", "\n")

            # Get current cursor position
            current_pos = self.last_focused_widget.index(tk.INSERT)

            # Insert the processed text
            self.last_focused_widget.insert(current_pos, processed_text)

            # Force a redraw and highlight
            self.last_focused_widget.update_idletasks()
            self.last_focused_widget.highlight_text()
        return "break"

    def copy_text(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            # Save the selection information before potentially losing focus
            has_selection = False
            selected_text = ""

            try:
                # Check if there's a selection and get it
                if self.last_focused_widget.tag_ranges(tk.SEL):
                    has_selection = True
                    selected_text = self.last_focused_widget.get(
                        tk.SEL_FIRST,
                        tk.SEL_LAST,
                    )
            except tk.TclError:
                # No selection
                has_selection = False

            # If we had a selection, use that text
            if has_selection:
                text = selected_text
            else:
                # Otherwise get all text
                text = self.last_focused_widget.get("1.0", tk.END).strip()

            # Copy to clipboard
            pyperclip.copy(text)

            # Restore focus to the text widget
            self.last_focused_widget.focus_set()

    def copy_code_block(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            # Save current cursor position
            current_cursor_pos = self.last_focused_widget.index(tk.INSERT)

            text = self.last_focused_widget.get("1.0", tk.END)
            cursor_pos = self.last_focused_widget.index(tk.INSERT)
            line, col = map(int, cursor_pos.split("."))

            # Parse all code blocks in the text
            code_blocks = parse_code_blocks(text)

            # Find all code blocks that contain the current cursor line
            containing_blocks = []
            for indent_level, language, start_line, end_line in code_blocks:
                if start_line <= line <= end_line:
                    containing_blocks.append(
                        (indent_level, language, start_line, end_line),
                    )

            if containing_blocks:
                # Sort blocks by size (smallest first) to find the most specific block
                containing_blocks.sort(key=lambda block: block[3] - block[2])

                # Get the smallest block that contains the cursor
                indent_level, language, start_line, end_line = containing_blocks[0]

                # Extract the code content
                start_index = f"{start_line}.0"
                end_index = f"{end_line}.end"
                code_content = self.last_focused_widget.get(start_index, end_index)

                # Clean the code content (remove backticks and language specifier)
                lines = code_content.split("\n")

                # Remove the opening and closing backtick lines
                if lines and lines[0].strip().startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]

                # Remove any common leading whitespace
                if lines:
                    non_empty_lines = [line for line in lines if line.strip()]
                    if non_empty_lines:
                        min_indent = min(
                            len(line) - len(line.lstrip()) for line in non_empty_lines
                        )
                        lines = [
                            line[min_indent:] if line.strip() else line
                            for line in lines
                        ]

                cleaned_code = "\n".join(lines)

                pyperclip.copy(cleaned_code)
                print(f"Code block copied to clipboard! Language: {language}")

                # Highlight the code block
                self.last_focused_widget.highlight_code_block(start_index, end_index)

                # Restore cursor position after highlighting
                self.last_focused_widget.mark_set(tk.INSERT, current_cursor_pos)
                self.last_focused_widget.see(current_cursor_pos)

                # Ensure the widget regains focus
                self.last_focused_widget.focus_set()
                return

            print("No code block found at the current cursor position.")

    def update_tab_name(self, tab: ChatTab, summary: str) -> None:
        tab_index = self.tabs.index(tab)
        self.notebook.tab(tab_index, text=summary)

        # Update window title if this is the currently selected tab
        current_tab_index = self.notebook.index(self.notebook.select())
        if tab_index == current_tab_index:
            self.master.title(f"Alpaca Assist - {summary}")

    def fetch_api_response_summary(
        self,
        tab: ChatTab,
        summary_queue: queue.Queue,
    ) -> None:
        """Fetch a summary of the conversation."""
        try:
            # Get current state from ChatState (thread-safe)
            questions, answers, _ = tab.chat_state.get_safe_copy()

            # Create a summary prompt from the first question and answer
            if questions and answers and questions[0].strip() and answers[0].strip():
                first_q = questions[0]
                first_a = answers[0][:500]  # Limit answer length for summary

                summary_prompt = f"Please provide a very brief summary (3-5 words) of this conversation:\n\nQ: {first_q}\nA: {first_a}"

                messages = [{"role": "user", "content": summary_prompt}]

                payload = {
                    "model": self.preferences["summary_model"],
                    "messages": messages,
                    "stream": True,  # Enable streaming for summaries
                }

                print(
                    f"Requesting summary with model: {self.preferences['summary_model']}",
                )

                # Make the streaming API request
                with requests.post(
                    BASE_URL,
                    json=payload,
                    stream=True,
                    timeout=30,
                ) as response:

                    if response.status_code != 200:
                        print(f"Summary API error: Status {response.status_code}")
                        print(f"Response: {response.text}")
                        summary_queue.put("Chat Summary")
                        return

                    # Process streaming response
                    accumulated_summary = ""

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
                                # Clean and limit summary length
                                summary = accumulated_summary.strip().replace(
                                    "\n",
                                    " ",
                                )[:50]
                                if not summary:
                                    summary = "Chat Summary"
                                print(f"Summary generated: {summary}")
                                summary_queue.put(summary)
                                return

                        except json.JSONDecodeError as json_err:
                            print(
                                f"Failed to decode JSON line in summary: {line}. Error: {json_err}",
                            )
                            continue
                        except Exception as content_err:
                            print(
                                f"Error processing summary content chunk: {content_err}",
                            )
                            continue

                    # If we get here, the stream ended without a "done" flag
                    if accumulated_summary:
                        summary = accumulated_summary.strip().replace("\n", " ")[:50]
                        print(f"Summary generated (no done flag): {summary}")
                        summary_queue.put(summary)
                    else:
                        print("No summary content received")
                        summary_queue.put("Chat Summary")

            else:
                print("No valid question/answer pair for summary")
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

    def fetch_api_response(self, tab: ChatTab, answer_index: int) -> None:
        """
        Fetch API response for a specific answer index using queue-based updates.
        """
        try:
            # Get the data payload for this request
            data_payload: dict[str, Any] = tab.input_queue.get(timeout=3)
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
            messages: List[dict[str, str]] = []

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
            ollama_payload: dict[str, Any] = {
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
                    tab.content_update_queue.put(
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
                                tab.content_update_queue.put(
                                    ContentUpdate(
                                        answer_index=answer_index,
                                        content_chunk=content_chunk,
                                        is_done=False,
                                        is_error=False,
                                    ),
                                )

                        if data.get("done", False):
                            # Queue completion update
                            tab.content_update_queue.put(
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
            tab.content_update_queue.put(
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
            tab.content_update_queue.put(
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
            tab.content_update_queue.put(
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
            tab.content_update_queue.put(
                ContentUpdate(
                    answer_index=answer_index,
                    content_chunk=error_msg,
                    is_done=True,
                    is_error=True,
                ),
            )

    def _handle_api_error(
        self,
        tab: ChatTab,
        answer_index: int,
        error_message: str,
    ) -> None:
        """Handle API errors by updating the answer with an error message."""
        error_content = f"\n\n[Error: {error_message}]"

        # Update the answer with error message
        tab.chat_state.append_to_answer(answer_index, error_content)

        # Mark streaming as finished
        tab.chat_state.finish_streaming()

        # Simple UI update using the same pattern
        self.master.after(0, tab.rebuild_display_from_state)

    def go_to_end_of_line(self, event: tk.Event) -> Optional[str]:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<End>")
            return "break"  # This prevents the default behavior
        return None

    def go_to_start_of_line(self, event: tk.Event) -> Optional[str]:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<Home>")
            return "break"  # This prevents the default behavior
        return None


def main() -> None:
    root = tk.Tk()
    app = ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
