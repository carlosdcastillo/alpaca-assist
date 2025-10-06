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

    def update_submit_button_text(self):
        """Update submit button text based on streaming state."""
        if self.is_streaming:
            self.submit_button.config(text="Stop")
        else:
            self.submit_button.config(text="Submit")

    def has_pending_api_requests(self) -> bool:
        """Check if there are pending API requests."""
        return not self.input_queue.empty()

    def rebuild_display_from_state(self):
        """Rebuild the entire display from ChatState (used for session loading)."""
        questions, answers, _ = self.chat_state.get_safe_copy()
        self.chat_display.set_server_mode(True)
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        for i, (question, answer) in enumerate(zip(questions, answers)):
            self.chat_display.insert(tk.END, f"Q: {question}\n")
            self.chat_display.insert(tk.END, f"A: {answer}\n")
            if i < len(questions) - 1:
                sep = "-" * 80
                self.chat_display.insert(tk.END, f"\n{sep}\n\n")
        self.chat_display.set_server_mode(False)
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.highlight_text()

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
        if avg_word_length > 6:
            tokens = char_count / 4.5
        elif avg_word_length < 3:
            tokens = char_count / 3.0
        else:
            tokens = char_count / 4.0
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

    def show_autocomplete_menu(
        self,
        filter_text: str = "",
        autocomplete_type: str = "file",
    ) -> None:
        """Display the autocomplete listbox with completions (file or prompt)."""
        self.autocomplete_type = autocomplete_type
        if autocomplete_type == "file":
            if filter_text:
                self.filtered_completions = [
                    comp
                    for comp in self.file_completions
                    if filter_text.lower() in os.path.basename(comp).lower()
                ]
            else:
                self.filtered_completions = self.file_completions.copy()
        elif autocomplete_type == "prompt":
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
        if autocomplete_type == "file" and (not self.filtered_completions):
            self.hide_autocomplete_menu()
            return
        elif autocomplete_type == "prompt" and (not self.filtered_prompts):
            self.hide_autocomplete_menu()
            return
        self.hide_autocomplete_menu()
        try:
            bbox_result = self.input_field.bbox("insert")
            if bbox_result is None:
                return
            x, y, _, h = bbox_result
        except tk.TclError:
            return
        self.autocomplete_window = tk.Toplevel(self.parent.master)
        self.autocomplete_window.wm_overrideredirect(True)
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
        try:
            input_abs_x = self.input_field.winfo_rootx() + x
            input_abs_y = self.input_field.winfo_rooty() + y + h + 2
            self.autocomplete_window.geometry(f"+{input_abs_x}+{input_abs_y}")
            if is_macos_system:
                self.autocomplete_window.update_idletasks()
                self.autocomplete_window.lift()
        except tk.TclError as e:
            print(f"Error positioning autocomplete window: {e}")
            self.hide_autocomplete_menu()
            return
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
        if autocomplete_type == "file":
            for completion in self.filtered_completions:
                display_name = os.path.basename(completion)
                self.autocomplete_listbox.insert(tk.END, display_name)
        else:
            for trigger, description in self.filtered_prompts:
                display_text = f"{trigger} - {description}" if description else trigger
                self.autocomplete_listbox.insert(tk.END, display_text)
        if num_items > 0:
            self.autocomplete_listbox.selection_set(0)
            self.autocomplete_listbox.activate(0)
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

    def check_for_autocomplete(self, event: tk.Event) -> None:
        """Check if we should show autocomplete and filter based on typed text."""
        if (
            self.autocomplete_window
            and self.autocomplete_listbox
            and (event.type == tk.EventType.KeyPress)
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
                return
            elif event.keysym == "Escape":
                self.hide_autocomplete_menu()
                return
        if event.type == tk.EventType.KeyRelease and event.keysym not in [
            "Down",
            "Up",
            "Return",
            "Escape",
            "Left",
            "Right",
        ]:
            current_line = self.input_field.get("insert linestart", "insert")
            if event.char in [":", "/"]:
                if current_line.endswith("/file:") or current_line.endswith("/file"):
                    self.file_trigger_position = self.input_field.index("insert")
                    self.prompt_trigger_position = None
                    self.show_autocomplete_menu(autocomplete_type="file")
                    return
                elif current_line.endswith("/prompt:") or current_line.endswith(
                    "/prompt",
                ):
                    self.prompt_trigger_position = self.input_field.index("insert")
                    self.file_trigger_position = None
                    self.show_autocomplete_menu(autocomplete_type="prompt")
                    return
            file_match = re.search("/file:([^/\\s]*)", current_line)
            if file_match:
                typed_text = file_match.group(1)
                colon_pos = current_line.rfind("/file:") + 6
                self.file_trigger_position = f"insert linestart + {colon_pos}c"
                self.prompt_trigger_position = None
                self.show_autocomplete_menu(typed_text, autocomplete_type="file")
                return
            prompt_match = re.search("/prompt:([^/\\s]*)", current_line)
            if prompt_match:
                typed_text = prompt_match.group(1)
                colon_pos = current_line.rfind("/prompt:") + 8
                self.prompt_trigger_position = f"insert linestart + {colon_pos}c"
                self.file_trigger_position = None
                self.show_autocomplete_menu(typed_text, autocomplete_type="prompt")
                return
            self.hide_autocomplete_menu()
            self.file_trigger_position = None
            self.prompt_trigger_position = None

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

    def load_from_data(self, data: dict[str, Any]) -> None:
        """Load data from serialized format."""
        if "chat_state" in data:
            self.chat_state = ChatState.from_dict(data["chat_state"])
        else:
            questions = data.get("chat_history_questions", [])
            answers_data = data.get("chat_history_answers", [])
            from chat_state import FullAnswer

            answers = [
                FullAnswer.from_string(answer) if isinstance(answer, str) else answer
                for answer in answers_data
            ]
            self.chat_state = ChatState(questions, answers)
        self.chat_history_questions = self.chat_state.questions.copy()
        self.chat_history_answers = [
            answer.get_text_content() for answer in self.chat_state.answers
        ]
        self.summary_generated = data.get("summary_generated", False)
        if "original_conversation_id" in data:
            self.original_conversation_id = data["original_conversation_id"]
        if "created_date" in data:
            self.created_date = data["created_date"]
        if (
            not self.summary_generated
            and self.chat_state.questions
            and self.chat_state.answers
            and self.chat_state.answers[0].get_text_content().strip()
        ):
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

    def create_widgets(self) -> None:
        chat_frame = ttk.Frame(self.frame)
        chat_frame.pack(expand=True, fill="both", padx=10, pady=10)
        self.paned_window = ttk.PanedWindow(chat_frame, orient=tk.VERTICAL)
        self.paned_window.pack(expand=True, fill="both")
        chat_display_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(chat_display_frame, weight=3)
        self.chat_display = SyntaxHighlightedText(
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
        self.chat_display.bind("<FocusIn>", self.parent.update_last_focused)
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
        self.input_field.bind("<FocusIn>", self.parent.update_last_focused)
        self._last_input_highlight_time = 0.0
        self._input_highlight_throttle = 0.15
        self.input_field.bind("<KeyRelease>", self._handle_input_key_release)
        self.input_field.bind("<KeyPress>", self._handle_input_key_press)
        self.input_field.bind("<FocusOut>", lambda e: self.hide_autocomplete_menu())
        self.input_field.bind("<Button-1>", lambda e: self.hide_autocomplete_menu())
        button_frame = ttk.Frame(input_frame)
        button_frame.pack(side="left", padx=5, fill="y")
        if is_macos():
            self.submit_button = tk.Button(
                button_frame,
                text="Submit",
                command=self.submit_message,
                height=2,
            )
            self.submit_button.pack(pady=2, fill="x")
            ToolTip(self.submit_button, "Submit (Ctrl+Enter)")
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
            self.submit_button = ttk.Button(
                button_frame,
                text="Submit",
                command=self.submit_message,
                style="Custom.TButton",
            )
            self.submit_button.pack(pady=2, fill="x")
            ToolTip(self.submit_button, "Submit (Ctrl+Enter)")
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
