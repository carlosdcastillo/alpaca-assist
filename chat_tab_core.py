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

        # Keep legacy lists for backward compatibility during transition
        self.chat_history_questions: list[str] = []
        self.chat_history_answers: list[str] = []

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

        # Add these new attributes for autocomplete tracking
        self.autocomplete_window: tk.Toplevel | None = None
        self.autocomplete_listbox: tk.Listbox | None = None
        self.file_trigger_position: str | None = None
        self.filtered_completions: list[str] = []

        self.content_update_queue: queue.Queue[ContentUpdate] = queue.Queue()
        self.answer_end_positions: dict[int, str] = {}  # Track where each answer ends

        # Thread safety for queue processor
        self._processor_lock = threading.Lock()
        self._queue_processor_running: bool = False

        self.is_streaming: bool = False
        self.current_request_thread: threading.Thread | None = None
        self.stop_streaming_flag: threading.Event = threading.Event()

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
            # Use ttk.Button for Windows and other platforms
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

    def update_submit_button_text(self):
        """Update submit button text based on streaming state."""
        if self.is_streaming:
            self.submit_button.config(text="Stop")
        else:
            self.submit_button.config(text="Submit")

    def has_pending_api_requests(self) -> bool:
        """Check if there are pending API requests."""
        return not self.input_queue.empty()

    def get_serializable_data(self) -> dict[str, Any]:
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

        # Use server mode for all loaded content
        self.chat_display.set_server_mode(True)
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)

        for i, (question, answer) in enumerate(zip(questions, answers)):
            self.chat_display.insert(tk.END, f"Q: {question}\n")
            self.chat_display.insert(tk.END, f"A: {answer}\n")

            if i < len(questions) - 1:
                sep = "-" * 80
                self.chat_display.insert(tk.END, f"\n{sep}\n\n")

        # Disable server mode after loading
        self.chat_display.set_server_mode(False)
        self.chat_display.config(state=tk.NORMAL)

        self.chat_display.highlight_text()

    def update_file_completions(self, new_completions: list[str]) -> None:
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

    def _insert_structural_content(self, content: str, position: str = tk.END) -> None:
        """Insert structural content (Q:, A:, separators) that should not be undoable."""
        self.chat_display.set_server_mode(True)
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(position, content)
        self.chat_display.set_server_mode(False)
