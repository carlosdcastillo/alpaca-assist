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
from markdown_viewer_adapter import MarkdownViewerAdapter
from syntax_text import SyntaxHighlightedText
from tooltip import ToolTip
from utils import ContentUpdate
from utils import is_macos

BASE_URL: str = "http://localhost:11434/api/chat"


class ChatTabCore:
    """Core functionality for ChatTab - initialization, UI setup, and basic operations."""

    def cleanup_resources(self):
        """Add this method - call when tab is destroyed"""
        with self._processor_lock:
            self._queue_processor_running = False
        while not self.content_update_queue.empty():
            try:
                self.content_update_queue.get_nowait()
            except queue.Empty:
                break
        self.hide_autocomplete_menu()

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
        self.check_for_autocomplete(event)
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

    def has_pending_api_requests(self) -> bool:
        """Check if there are pending API requests."""
        return not self.input_queue.empty()

    def update_file_completions(self, new_completions: list[str]) -> None:
        self.file_completions = new_completions

    def _force_listbox_redraw(self) -> None:
        """Force the listbox to redraw - macOS workaround."""
        if self.autocomplete_listbox and self.autocomplete_window:
            try:
                self.autocomplete_listbox.update_idletasks()
                self.autocomplete_listbox.update()
                current_selection = self.autocomplete_listbox.curselection()
                if current_selection:
                    idx = current_selection[0]
                    self.autocomplete_listbox.selection_clear(0, tk.END)
                    self.autocomplete_listbox.selection_set(idx)
                    self.autocomplete_listbox.activate(idx)
                    self.autocomplete_listbox.see(idx)
                self.autocomplete_window.update_idletasks()
            except tk.TclError:
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

    def go_to_end_of_line(self, event: tk.Event) -> str:
        widget = event.widget
        widget.mark_set(tk.INSERT, "insert lineend")
        return "break"

    def go_to_start_of_line(self, event: tk.Event) -> str:
        widget = event.widget
        widget.mark_set(tk.INSERT, "insert linestart")
        return "break"

    def _on_chat_display_text_change(self, event: tk.Event = None) -> None:
        """Handle text changes in the chat display area ONLY."""
        current_time = time.time()
        if hasattr(self, "_last_status_update_time"):
            if current_time - self._last_status_update_time < 0.2:
                return
        self._last_status_update_time = current_time
        self.parent.master.after_idle(self.update_status_bar)

    def _calculate_token_multiplier(self, avg_word_length: float) -> float:
        """Calculate token multiplier based on average word length."""
        if avg_word_length > 6:
            return 4.5
        elif avg_word_length < 3:
            return 3.0
        else:
            return 4.0

    def estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens in the text using a simple heuristic.

        This uses a rough approximation:
        - Average of ~4 characters per token for English text
        - Adjustments for whitespace and punctuation
        - This is an estimate and may not match exact tokenizer results

        Args:
            text: The text to estimate tokens for

        Returns:
            Estimated number of tokens
        """
        if not text.strip():
            return 0
        normalized_text = " ".join(text.split())
        char_count = len(normalized_text)
        word_count = len(normalized_text.split())
        if word_count == 0:
            return 0
        avg_word_length = char_count / word_count if word_count > 0 else 0

        # Use helper method to reduce complexity
        multiplier = self._calculate_token_multiplier(avg_word_length)
        tokens = char_count / multiplier

        punctuation_count = sum(
            1 for c in text if not c.isalnum() and (not c.isspace())
        )
        tokens += punctuation_count * 0.3
        return max(1, int(round(tokens)))

    def get_text_stats(
        self,
        widget: SyntaxHighlightedText,
    ) -> tuple[int, int, int, int]:
        """Calculate character count, line count, byte size, and token estimate for a text widget.

        Returns:
            tuple: (character_count, line_count, byte_size, estimated_tokens)
        """
        try:
            text = widget.get("1.0", tk.END)
            if text.endswith("\n"):
                text = text[:-1]
            char_count = len(text)
            line_count = text.count("\n") + 1 if text else 0
            byte_size = len(text.encode("utf-8"))
            token_estimate = self.estimate_tokens(text)
            return (char_count, line_count, byte_size, token_estimate)
        except tk.TclError:
            return (0, 0, 0, 0)

    def _insert_structural_content(self, content: str, position: str = tk.END) -> None:
        """Insert structural content (Q:, A:, separators) that should not be undoable."""
        self.chat_display.set_server_mode(True)
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(position, content)
        self.chat_display.set_server_mode(False)

    def _prepare_filtered_completions(
        self,
        filter_text: str,
        autocomplete_type: str,
    ) -> None:
        """Prepare filtered completions based on type and filter text."""
        if autocomplete_type == "file":
            self._prepare_file_completions(filter_text)
        elif autocomplete_type == "prompt":
            self._prepare_prompt_completions(filter_text)

    def _prepare_file_completions(self, filter_text: str) -> None:
        """Prepare file completions with filtering."""
        if filter_text:
            self.filtered_completions = [
                comp
                for comp in self.file_completions
                if filter_text.lower() in os.path.basename(comp).lower()
            ]
        else:
            self.filtered_completions = self.file_completions.copy()

    def _prepare_prompt_completions(self, filter_text: str) -> None:
        """Prepare prompt completions with filtering."""
        if hasattr(self.parent, "prompt_manager"):
            all_prompts = self.parent.prompt_manager.get_prompts_for_autocomplete()
            if filter_text:
                self.filtered_prompts = [
                    (trigger, desc)
                    for trigger, desc in all_prompts
                    if filter_text.lower() in trigger.lower()
                    or filter_text.lower() in desc.lower()
                ]
            else:
                self.filtered_prompts = all_prompts
        else:
            self.filtered_prompts = []

    def _should_hide_autocomplete(self, autocomplete_type: str) -> bool:
        """Check if autocomplete should be hidden due to empty completions."""
        if autocomplete_type == "file":
            return not self.filtered_completions
        elif autocomplete_type == "prompt":
            return not self.filtered_prompts
        return False

    def _get_autocomplete_window_position(self) -> tuple[int, int] | None:
        """Get the position for the autocomplete window."""
        try:
            bbox_result = self.input_field.bbox("insert")
            if bbox_result is None:
                return None
            x, y, _, h = bbox_result
            input_abs_x = self.input_field.winfo_rootx() + x
            input_abs_y = self.input_field.winfo_rooty() + y + h + 2
            return (input_abs_x, input_abs_y)
        except tk.TclError:
            return None

    def _configure_autocomplete_window(self, is_macos_system: bool) -> None:
        """Configure the autocomplete window appearance."""
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

    def _get_listbox_config(
        self,
        autocomplete_type: str,
        is_macos_system: bool,
    ) -> dict:
        """Get configuration for the autocomplete listbox."""
        font_family = str(self.preferences["font_family"])
        font_size = int(str(self.preferences["font_size"]))

        if autocomplete_type == "file":
            num_items = len(self.filtered_completions)
        else:
            num_items = len(self.filtered_prompts)

        listbox_config = {
            "height": min(8, num_items),
            "width": 60 if autocomplete_type == "prompt" else 50,
            "font": (font_family, font_size),
            "exportselection": False,
            "activestyle": "none",
        }

        self._apply_platform_listbox_styles(listbox_config, is_macos_system)
        return listbox_config

    def _apply_platform_listbox_styles(
        self,
        config: dict,
        is_macos_system: bool,
    ) -> None:
        """Apply platform-specific styles to listbox config."""
        if is_macos_system:
            config.update(
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
            config.update(
                {
                    "bg": "white",
                    "fg": "black",
                    "selectbackground": "#0078d4",
                    "selectforeground": "white",
                },
            )

    def _populate_listbox(self, autocomplete_type: str) -> None:
        """Populate the listbox with completion items."""
        if autocomplete_type == "file":
            for completion in self.filtered_completions:
                display_name = os.path.basename(completion)
                self.autocomplete_listbox.insert(tk.END, display_name)
        else:
            for trigger, description in self.filtered_prompts:
                display_text = f"{trigger} - {description}" if description else trigger
                self.autocomplete_listbox.insert(tk.END, display_text)

    def _finalize_autocomplete_window(
        self,
        is_macos_system: bool,
        num_items: int,
    ) -> None:
        """Finalize the autocomplete window setup."""
        if num_items > 0:
            self.autocomplete_listbox.selection_set(0)
            self.autocomplete_listbox.activate(0)

        self.autocomplete_listbox.bind("<Double-Button-1>", self.on_autocomplete_select)
        self.autocomplete_listbox.bind("<Return>", self.on_autocomplete_select)

        if is_macos_system:
            self._finalize_macos_autocomplete()
        else:
            self.autocomplete_window.update_idletasks()

    def _finalize_macos_autocomplete(self) -> None:
        """Finalize autocomplete window for macOS."""
        self.autocomplete_window.update_idletasks()
        self.autocomplete_listbox.update_idletasks()
        self.autocomplete_window.lift()
        self.autocomplete_window.attributes("-topmost", True)
        self.parent.master.after(1, self._force_listbox_redraw)
        self.parent.master.after(10, self._force_listbox_redraw)
        self.parent.master.after(50, self._force_listbox_redraw)

    def show_autocomplete_menu(
        self,
        filter_text: str = "",
        autocomplete_type: str = "file",
    ) -> None:
        """Display the autocomplete listbox with completions (file or prompt)."""
        self.autocomplete_type = autocomplete_type

        # Prepare filtered completions
        self._prepare_filtered_completions(filter_text, autocomplete_type)

        # Check if we should hide due to empty completions
        if self._should_hide_autocomplete(autocomplete_type):
            self.hide_autocomplete_menu()
            return

        self.hide_autocomplete_menu()

        # Get window position
        position = self._get_autocomplete_window_position()
        if position is None:
            return
        input_abs_x, input_abs_y = position

        # Create and configure window
        self.autocomplete_window = tk.Toplevel(self.parent.master)
        self.autocomplete_window.wm_overrideredirect(True)
        is_macos_system = self.parent.master.tk.call("tk", "windowingsystem") == "aqua"

        self._configure_autocomplete_window(is_macos_system)

        # Position window
        if not self._position_autocomplete_window(
            input_abs_x,
            input_abs_y,
            is_macos_system,
        ):
            return

        # Create and configure listbox
        self._create_autocomplete_listbox(autocomplete_type, is_macos_system)

        # Populate and finalize
        self._populate_listbox(autocomplete_type)
        num_items = (
            len(self.filtered_completions)
            if autocomplete_type == "file"
            else len(self.filtered_prompts)
        )
        self._finalize_autocomplete_window(is_macos_system, num_items)

    def _position_autocomplete_window(
        self,
        x: int,
        y: int,
        is_macos_system: bool,
    ) -> bool:
        """Position the autocomplete window. Returns True if successful."""
        try:
            self.autocomplete_window.geometry(f"+{x}+{y}")
            if is_macos_system:
                self.autocomplete_window.update_idletasks()
                self.autocomplete_window.lift()
            return True
        except tk.TclError as e:
            print(f"Error positioning autocomplete window: {e}")
            self.hide_autocomplete_menu()
            return False

    def _create_autocomplete_listbox(
        self,
        autocomplete_type: str,
        is_macos_system: bool,
    ) -> None:
        """Create and configure the autocomplete listbox."""
        listbox_config = self._get_listbox_config(autocomplete_type, is_macos_system)
        self.autocomplete_listbox = tk.Listbox(
            self.autocomplete_window,
            **listbox_config,
        )
        self.autocomplete_listbox.pack(padx=2, pady=2, fill="both", expand=True)

    def _handle_navigation_keys(self, event: tk.Event) -> bool:
        """Handle navigation keys in autocomplete menu. Returns True if handled."""
        if event.keysym == "Down":
            self._navigate_autocomplete_down()
            return True
        elif event.keysym == "Up":
            self._navigate_autocomplete_up()
            return True
        return False

    def _navigate_autocomplete_down(self) -> None:
        """Navigate down in autocomplete menu."""
        current = self.autocomplete_listbox.curselection()
        if current:
            next_index = min(current[0] + 1, self.autocomplete_listbox.size() - 1)
        else:
            next_index = 0
        self._set_autocomplete_selection(next_index)

    def _navigate_autocomplete_up(self) -> None:
        """Navigate up in autocomplete menu."""
        current = self.autocomplete_listbox.curselection()
        if current:
            prev_index = max(current[0] - 1, 0)
        else:
            prev_index = 0
        self._set_autocomplete_selection(prev_index)

    def _set_autocomplete_selection(self, index: int) -> None:
        """Set the autocomplete selection to the given index."""
        self.autocomplete_listbox.selection_clear(0, tk.END)
        self.autocomplete_listbox.selection_set(index)
        self.autocomplete_listbox.activate(index)
        self.autocomplete_listbox.see(index)

    def _handle_selection_keys(self, event: tk.Event) -> bool:
        """Handle selection keys in autocomplete menu. Returns True if handled."""
        if event.keysym == "Return":
            self._handle_autocomplete_return()
            return True
        elif event.keysym == "Escape":
            self.hide_autocomplete_menu()
            return True
        return False

    def _handle_autocomplete_return(self) -> None:
        """Handle return key in autocomplete menu."""
        selection = self.autocomplete_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        if self.autocomplete_type == "file" and 0 <= index < len(
            self.filtered_completions,
        ):
            selected_file = self.filtered_completions[index]
            self.insert_completion(selected_file)
        elif self.autocomplete_type == "prompt" and 0 <= index < len(
            self.filtered_prompts,
        ):
            trigger, _ = self.filtered_prompts[index]
            self.insert_prompt_completion(trigger)

    def _check_trigger_patterns(self, current_line: str, event: tk.Event) -> bool:
        """Check for trigger patterns in current line. Returns True if handled."""
        if event.char not in [":", "/"]:
            return False

        if current_line.endswith("/file:") or current_line.endswith("/file"):
            self.file_trigger_position = self.input_field.index("insert")
            self.prompt_trigger_position = None
            self.show_autocomplete_menu(autocomplete_type="file")
            return True
        elif current_line.endswith("/prompt:") or current_line.endswith("/prompt"):
            self.prompt_trigger_position = self.input_field.index("insert")
            self.file_trigger_position = None
            self.show_autocomplete_menu(autocomplete_type="prompt")
            return True
        return False

    def _check_file_match(self, current_line: str) -> bool:
        """Check for file pattern matches. Returns True if handled."""
        file_match = re.search("/file:([^/\\s]*)", current_line)
        if file_match:
            typed_text = file_match.group(1)
            colon_pos = current_line.rfind("/file:") + 6
            self.file_trigger_position = f"insert linestart + {colon_pos}c"
            self.prompt_trigger_position = None
            self.show_autocomplete_menu(typed_text, autocomplete_type="file")
            return True
        return False

    def _check_prompt_match(self, current_line: str) -> bool:
        """Check for prompt pattern matches. Returns True if handled."""
        prompt_match = re.search("/prompt:([^/\\s]*)", current_line)
        if prompt_match:
            typed_text = prompt_match.group(1)
            colon_pos = current_line.rfind("/prompt:") + 8
            self.prompt_trigger_position = f"insert linestart + {colon_pos}c"
            self.file_trigger_position = None
            self.show_autocomplete_menu(typed_text, autocomplete_type="prompt")
            return True
        return False

    def _handle_existing_autocomplete_window(self, event: tk.Event) -> bool:
        """Handle events when autocomplete window exists. Returns True if handled."""
        if not (self.autocomplete_window and self.autocomplete_listbox):
            return False

        if event.type != tk.EventType.KeyPress:
            return False

        # Handle navigation keys
        if self._handle_navigation_keys(event):
            return True

        # Handle selection keys
        if self._handle_selection_keys(event):
            return True

        return False

    def _handle_new_autocomplete_triggers(self, event: tk.Event) -> None:
        """Handle new autocomplete triggers on key release."""
        if event.type != tk.EventType.KeyRelease:
            return

        if event.keysym in ["Down", "Up", "Return", "Escape", "Left", "Right"]:
            return

        current_line = self.input_field.get("insert linestart", "insert")

        # Check trigger patterns
        if self._check_trigger_patterns(current_line, event):
            return

        # Check file match
        if self._check_file_match(current_line):
            return

        # Check prompt match
        if self._check_prompt_match(current_line):
            return

        # No matches found, hide autocomplete
        self.hide_autocomplete_menu()
        self.file_trigger_position = None
        self.prompt_trigger_position = None

    def check_for_autocomplete(self, event: tk.Event) -> None:
        """Check if we should show autocomplete and filter based on typed text."""
        # Handle existing autocomplete window
        if self._handle_existing_autocomplete_window(event):
            return

        # Handle new autocomplete triggers
        self._handle_new_autocomplete_triggers(event)

    def insert_completion(self, option: str) -> None:
        """Insert the selected file completion, replacing any partially typed text."""
        if self.file_trigger_position:
            self.input_field.delete(self.file_trigger_position, tk.INSERT)
            self.input_field.insert(self.file_trigger_position, option + " ")
        else:
            cursor_position = self.input_field.index(tk.INSERT)
            self.input_field.insert(cursor_position, option + " ")
        self.hide_autocomplete_menu()
        self.file_trigger_position = None
        self.input_field.focus_set()

    def insert_prompt_completion(self, trigger: str) -> None:
        """Insert the selected prompt completion, replacing any partially typed text."""
        if self.prompt_trigger_position:
            self.input_field.delete(self.prompt_trigger_position, tk.INSERT)
            self.input_field.insert(self.prompt_trigger_position, trigger + " ")
        else:
            cursor_position = self.input_field.index(tk.INSERT)
            self.input_field.insert(cursor_position, trigger + " ")
        self.hide_autocomplete_menu()
        self.prompt_trigger_position = None
        self.input_field.focus_set()

    def update_status_bar(self) -> None:
        """Update the status bar with current chat display statistics and streaming status."""
        try:
            char_count, line_count, byte_size, token_estimate = self.get_text_stats(
                self.chat_display,
            )
            streaming_status = "🟢 Streaming" if self.is_streaming else "⚪ Idle"
            status_text = f"Chat: {char_count:,} chars, {line_count:,} lines, {byte_size:,} bytes, ~{token_estimate:,} tokens | {streaming_status}"
            if hasattr(self, "status_var"):
                self.status_var.set(status_text)
        except Exception as e:
            if hasattr(self, "status_var"):
                self.status_var.set("Status: Error calculating statistics")

    def _load_chat_state_from_data(self, data: dict[str, Any]) -> None:
        """Load chat state from data dictionary."""
        if "chat_state" in data:
            self.chat_state = ChatState.from_dict(data["chat_state"])
        else:
            self._load_legacy_chat_state(data)

    def _load_legacy_chat_state(self, data: dict[str, Any]) -> None:
        """Load legacy chat state format."""
        questions = data.get("chat_history_questions", [])
        answers_data = data.get("chat_history_answers", [])
        from chat_state import FullAnswer

        answers = [
            FullAnswer.from_string(answer) if isinstance(answer, str) else answer
            for answer in answers_data
        ]
        self.chat_state = ChatState(questions, answers)

    def _load_additional_data(self, data: dict[str, Any]) -> None:
        """Load additional data fields."""
        self.summary_generated = data.get("summary_generated", False)
        if "original_conversation_id" in data:
            self.original_conversation_id = data["original_conversation_id"]
        if "created_date" in data:
            self.created_date = data["created_date"]

    def _should_generate_summary(self) -> bool:
        """Check if summary should be generated."""
        return (
            not self.summary_generated
            and self.chat_state.questions
            and self.chat_state.answers
            and self.chat_state.answers[0].get_text_content().strip()
        )

    def load_from_data(self, data: dict[str, Any]) -> None:
        """Load data from serialized format."""
        # Load chat state
        self._load_chat_state_from_data(data)

        # Update history lists
        self.chat_history_questions = self.chat_state.questions.copy()
        self.chat_history_answers = [
            answer.get_text_content() for answer in self.chat_state.answers
        ]

        # Load additional data
        self._load_additional_data(data)

        # Generate summary if needed
        if self._should_generate_summary():
            self.parent.master.after(1000, lambda: self.get_summary())

    def get_serializable_data(self) -> dict[str, Any]:
        """Get data for serialization."""
        answer_strings = [
            answer.get_text_content() for answer in self.chat_state.answers
        ]
        data = {
            "chat_state": self.chat_state.to_dict(),
            "summary_generated": self.summary_generated,
            "chat_history_questions": self.chat_state.questions.copy(),
            "chat_history_answers": answer_strings,
        }
        if not hasattr(self, "created_date"):
            self.created_date = datetime.now().isoformat()
        data["created_date"] = self.created_date
        if hasattr(self, "original_conversation_id"):
            data["original_conversation_id"] = self.original_conversation_id
        return data

    def __init__(
        self,
        parent: "ChatApp",
        notebook: ttk.Notebook,
        file_completions: list[str],
        preferences: dict[str, Any] | None = None,
    ) -> None:
        self.chat_state = ChatState([], [])
        self.parent = parent
        self.notebook = notebook
        self.preferences = preferences or parent.preferences
        self.input_queue: queue.Queue = queue.Queue()
        self.last_update_time: float = 0.0
        self.update_throttle = float(
            str(self.preferences.get("chat_update_throttle", 0.1)),
        )
        self.frame: ttk.Frame = ttk.Frame(notebook)
        notebook.add(self.frame, text=f"Tab {len(parent.tabs) + 1}")
        self.create_widgets()
        self.summary_generated: bool = False
        self.file_completions: list[str] = file_completions
        self.chat_display: SyntaxHighlightedText
        self.input_field: SyntaxHighlightedText
        self.autocomplete_window: tk.Toplevel | None = None
        self.autocomplete_listbox: tk.Listbox | None = None
        self.file_trigger_position: str | None = None
        self.filtered_completions: list[str] = []
        self.prompt_trigger_position: str | None = None
        self.filtered_prompts: list[str] = []
        self.autocomplete_type: str | None = None
        self.content_update_queue: queue.Queue[ContentUpdate] = queue.Queue()
        self.answer_end_positions: dict[int, str] = {}
        self._processor_lock = threading.Lock()
        self._queue_processor_running: bool = False
        self.is_streaming: bool = False
        self.current_request_thread: threading.Thread | None = None
        self.stop_streaming_flag: threading.Event = threading.Event()

    def update_submit_button_text(self):
        """Update submit button text based on streaming state."""
        if self.is_streaming:
            self.submit_button.config(text="Stop")
        else:
            self.submit_button.config(text="Submit")
        self.update_status_bar()

    def _handle_focus_in(self, event: tk.Event) -> None:
        """Handle focus in events to update status bar."""
        self.parent.master.after_idle(self.update_status_bar)
        self.parent.update_last_focused(event)

    def create_widgets(self) -> None:
        chat_frame = ttk.Frame(self.frame)
        chat_frame.pack(expand=True, fill="both", padx=10, pady=10)
        self.paned_window = ttk.PanedWindow(chat_frame, orient=tk.VERTICAL)
        self.paned_window.pack(expand=True, fill="both")
        chat_display_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(chat_display_frame, weight=3)
        self.chat_display = MarkdownViewerAdapter(
            chat_display_frame,
            wrap=tk.WORD,
            height=20,
            theme_name=str(self.preferences["theme"]),
            background_color=str(self.preferences["background_color"]),
            font_family=str(self.preferences["font_family"]),
            font_size=int(str(self.preferences["font_size"])),
        )
        self.chat_display.tab = self
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
        self.chat_display.bind("<FocusIn>", self._handle_focus_in)
        self._last_chat_highlight_time = 0.0
        self.chat_display.bind("<KeyRelease>", self._handle_chat_display_key_release)
        self.chat_display.bind(
            "<KeyRelease>",
            self._on_chat_display_text_change,
            add="+",
        )
        self.chat_display.bind("<<Modified>>", self._on_chat_display_text_change)
        input_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(input_frame, weight=1)
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
        self.input_field.tab = self
        self.input_field.pack(side="left", expand=True, fill="both")
        self.input_field.bind(
            "<Control-Return>",
            lambda e: self.submit_message() or "break",
        )
        self.input_field.bind("<Control-e>", self.go_to_end_of_line)
        self.input_field.bind("<Control-a>", self.go_to_start_of_line)
        self.input_field.bind("<FocusIn>", self._handle_focus_in)
        self._last_input_highlight_time = 0.0
        self._input_highlight_throttle = 0.15
        self.input_field.bind("<KeyRelease>", self._handle_input_key_release)
        self.input_field.bind("<KeyPress>", self._handle_input_key_press)
        self.input_field.bind("<FocusOut>", lambda e: self.hide_autocomplete_menu())
        self.input_field.bind("<Button-1>", lambda e: self.hide_autocomplete_menu())
        button_frame = ttk.Frame(input_frame)
        button_frame.pack(side="left", padx=5, fill="y")
        self.submit_button = ttk.Button(
            button_frame,
            text="Submit",
            command=self.submit_message,
        )
        self.submit_button.pack(pady=2, fill="x")
        ToolTip(self.submit_button, "Submit (Ctrl+Enter)")
        paste_button = ttk.Button(
            button_frame,
            text="Paste",
            command=self.parent.paste_text,
        )
        paste_button.pack(pady=2, fill="x")
        ToolTip(paste_button, "Paste (Ctrl+V)")
        copy_button = ttk.Button(
            button_frame,
            text="Copy",
            command=self.parent.copy_text,
        )
        copy_button.pack(pady=2, fill="x")
        ToolTip(copy_button, "Copy (Ctrl+C)")
        copy_code_button = ttk.Button(
            button_frame,
            text="Copy Code",
            command=self.parent.copy_code_block,
        )
        copy_code_button.pack(pady=2, fill="x")
        ToolTip(copy_code_button, "Copy Code (Ctrl+B)")
        self.status_frame = ttk.Frame(self.frame)
        self.status_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.status_var = tk.StringVar()
        self.status_label = ttk.Label(
            self.status_frame,
            textvariable=self.status_var,
            font=("Arial", 13),
            foreground="gray",
        )
        self.status_label.pack(side="left")
        self.update_status_bar()

    def rebuild_display_from_state(self):
        """Rebuild the entire display from ChatState (used for session loading)."""
        questions, answers, _ = self.chat_state.get_safe_copy()
        self.chat_display.set_server_mode(True)
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        for i, (question, answer) in enumerate(zip(questions, answers)):
            self.chat_display.insert(tk.END, f"Q: {question}\n\n")
            self.chat_display.insert(tk.END, f"A: {answer}\n\n")
            if i < len(questions) - 1:
                sep = "-" * 80
                self.chat_display.insert(tk.END, f"\n{sep}\n\n")
        self.chat_display.set_server_mode(False)
        self.chat_display.config(state=tk.NORMAL)

        # Apply markdown formatting to the pre-loaded content
        if hasattr(self.chat_display, "finalize_rendering"):
            self.chat_display.finalize_rendering()

        self.chat_display.highlight_text()
