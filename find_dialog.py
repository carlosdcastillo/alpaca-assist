import platform
import re
import time
import tkinter as tk
from tkinter import ttk
from typing import List
from typing import Optional
from typing import Tuple


class FindDialog:
    def __init__(self, parent: tk.Tk, text_widget: tk.Text):
        self.parent = parent
        self.text_widget = text_widget
        self.dialog: tk.Toplevel | None = None
        self.search_var = tk.StringVar()
        self.case_sensitive_var = tk.BooleanVar()
        self.whole_word_var = tk.BooleanVar()
        self.current_match_index = 0
        self.matches: list[tuple[str, str]] = []
        self.highlight_tag = "find_highlight"
        self.current_tag = "find_current"

        # Performance optimization: cache content and line positions
        self._cached_content = ""
        self._cached_line_starts = []
        self._cache_valid = False
        self._search_timer = None
        self._search_delay = 200  # milliseconds

        # Configure highlight tags
        self.text_widget.tag_configure(
            self.highlight_tag,
            background="#FFFF00",
            foreground="#000000",
        )
        self.text_widget.tag_configure(
            self.current_tag,
            background="#FF8C00",
            foreground="#000000",
        )

    def _update_cache(self):
        """Update cached content and line positions if needed."""
        if not self._cache_valid:
            # Get content once and cache it
            self._cached_content = self.text_widget.get("1.0", "end-1c")

            # Pre-calculate line start positions for fast index conversion
            self._cached_line_starts = [0]
            for i, char in enumerate(self._cached_content):
                if char == "\n":
                    self._cached_line_starts.append(i + 1)

            self._cache_valid = True

    def _invalidate_cache(self):
        """Mark cache as invalid (call when text might have changed)."""
        self._cache_valid = False

    def char_to_tk_index_fast(self, char_pos: int) -> str | None:
        """Fast character position to Tkinter index conversion using cached data."""
        if char_pos < 0 or char_pos > len(self._cached_content):
            return None

        # Binary search to find the line containing char_pos
        line_num = 1
        for i, line_start in enumerate(self._cached_line_starts):
            if i + 1 < len(self._cached_line_starts):
                if char_pos < self._cached_line_starts[i + 1]:
                    line_num = i + 1
                    break
            else:
                line_num = i + 1
                break

        # Calculate column position
        line_start = self._cached_line_starts[line_num - 1]
        col_pos = char_pos - line_start

        return f"{line_num}.{col_pos}"

    def find_all_optimized(self):
        """Optimized find all with caching and efficient regex."""
        search_text = self.search_var.get()
        if not search_text:
            self.clear_highlights()
            return

        # Update cache if needed
        self._update_cache()

        # Clear previous highlights efficiently
        self.text_widget.tag_remove(self.highlight_tag, "1.0", tk.END)
        self.text_widget.tag_remove(self.current_tag, "1.0", tk.END)

        # Prepare search pattern
        if self.whole_word_var.get():
            pattern = r"\b" + re.escape(search_text) + r"\b"
        else:
            pattern = re.escape(search_text)

        flags = 0 if self.case_sensitive_var.get() else re.IGNORECASE

        # Compile regex once for better performance
        try:
            regex = re.compile(pattern, flags)
        except re.error:
            self.status_label.config(text="Invalid search pattern")
            return

        # Find all matches using the cached content
        self.matches = []
        for match in regex.finditer(self._cached_content):
            start_pos = self.char_to_tk_index_fast(match.start())
            end_pos = self.char_to_tk_index_fast(match.end())
            if start_pos and end_pos:
                self.matches.append((start_pos, end_pos))

        # Batch highlight all matches (more efficient than individual tag_add calls)
        if self.matches:
            # Temporarily disable text widget updates for better performance
            self.text_widget.config(state=tk.DISABLED)
            try:
                for start, end in self.matches:
                    self.text_widget.tag_add(self.highlight_tag, start, end)
            finally:
                self.text_widget.config(state=tk.NORMAL)

            # Set current match
            self.current_match_index = 0
            self.highlight_current_match()
            self.status_label.config(text=f"Found {len(self.matches)} matches")
        else:
            self.status_label.config(text="No matches found")

    def on_search_changed_throttled(self, *args):
        """Throttled search to avoid excessive searching while typing."""
        # Cancel previous timer
        if self._search_timer:
            self.parent.after_cancel(self._search_timer)

        # Schedule new search with delay
        search_text = self.search_var.get()
        if search_text and len(search_text) >= 2:  # Only search for 2+ characters
            self._search_timer = self.parent.after(
                self._search_delay,
                self.find_all_optimized,
            )
        elif not search_text:
            self.clear_highlights()

    def show(self) -> None:
        """Show the find dialog."""
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift()
            self.dialog.focus_set()
            return

        # Invalidate cache when dialog is shown (text might have changed)
        self._invalidate_cache()

        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Find")
        self.dialog.resizable(False, False)

        # Position relative to parent window
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Center on parent window
        self.parent.update_idletasks()
        x = self.parent.winfo_x() + (self.parent.winfo_width() // 2) - 225
        y = self.parent.winfo_y() + (self.parent.winfo_height() // 2) - 70
        self.dialog.geometry(f"450x140+{x}+{y}")

        # Create the widgets
        self.create_widgets()

        # Set focus to search entry
        self.search_entry.focus_set()

        # Bind events
        self.search_entry.bind("<Return>", lambda e: self.find_next())
        self.search_entry.bind("<Shift-Return>", lambda e: self.find_previous())
        self.search_entry.bind("<Escape>", lambda e: self.close())
        self.dialog.bind("<Escape>", lambda e: self.close())

        self.dialog.protocol("WM_DELETE_WINDOW", self.close)

    def create_widgets(self):
        """Create the dialog widgets."""
        # Main frame with reduced padding
        main_frame = ttk.Frame(self.dialog, padding="8")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Search frame
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_frame, text="Find:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(
            search_frame,
            textvariable=self.search_var,
            width=30,
        )
        self.search_entry.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)

        # Options frame
        options_frame = ttk.Frame(main_frame)
        options_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Checkbutton(
            options_frame,
            text="Case sensitive",
            variable=self.case_sensitive_var,
            command=self._on_option_changed,
        ).pack(side=tk.LEFT)

        ttk.Checkbutton(
            options_frame,
            text="Whole word",
            variable=self.whole_word_var,
            command=self._on_option_changed,
        ).pack(side=tk.LEFT, padx=(15, 0))

        # Buttons frame
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(fill=tk.X, pady=(0, 8))

        # Left side buttons
        left_buttons = ttk.Frame(buttons_frame)
        left_buttons.pack(side=tk.LEFT)

        ttk.Button(
            left_buttons,
            text="Find Next",
            command=self.find_next,
        ).pack(side=tk.LEFT)

        ttk.Button(
            left_buttons,
            text="Find Previous",
            command=self.find_previous,
        ).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Button(
            left_buttons,
            text="Find All",
            command=self.find_all_optimized,
        ).pack(side=tk.LEFT, padx=(5, 0))

        # Right side close button
        ttk.Button(
            buttons_frame,
            text="Close",
            command=self.close,
        ).pack(side=tk.RIGHT)

        # Status label
        self.status_label = ttk.Label(
            main_frame,
            text="",
            font=("TkDefaultFont", 9),
        )
        self.status_label.pack(pady=(5, 0), fill=tk.X)

        # Use throttled search handler
        self.search_var.trace("w", self.on_search_changed_throttled)

    def _on_option_changed(self):
        """Handle option changes (case sensitive, whole word)."""
        if self.search_var.get():
            # Re-search with new options, but throttled
            self.on_search_changed_throttled()

    def find_next(self):
        """Find next occurrence."""
        if not self.matches:
            self.find_all_optimized()
            return

        if self.matches:
            self.current_match_index = (self.current_match_index + 1) % len(
                self.matches,
            )
            self.highlight_current_match()
            self.scroll_to_current_match()

    def find_previous(self):
        """Find previous occurrence."""
        if not self.matches:
            self.find_all_optimized()
            return

        if self.matches:
            self.current_match_index = (self.current_match_index - 1) % len(
                self.matches,
            )
            self.highlight_current_match()
            self.scroll_to_current_match()

    def highlight_current_match(self):
        """Highlight the current match differently."""
        self.text_widget.tag_remove(self.current_tag, "1.0", tk.END)

        if self.matches and 0 <= self.current_match_index < len(self.matches):
            start, end = self.matches[self.current_match_index]
            self.text_widget.tag_add(self.current_tag, start, end)
            self.status_label.config(
                text=f"Match {self.current_match_index + 1} of {len(self.matches)}",
            )

    def scroll_to_current_match(self):
        """Scroll to show the current match."""
        if self.matches and 0 <= self.current_match_index < len(self.matches):
            start, _ = self.matches[self.current_match_index]
            self.text_widget.see(start)

    def clear_highlights(self):
        """Clear all search highlights."""
        self.text_widget.tag_remove(self.highlight_tag, "1.0", tk.END)
        self.text_widget.tag_remove(self.current_tag, "1.0", tk.END)
        self.matches = []
        self.current_match_index = 0
        self.status_label.config(text="")

    def close(self):
        """Close the find dialog."""
        if self.dialog:
            # Cancel any pending search
            if self._search_timer:
                self.parent.after_cancel(self._search_timer)

            self.clear_highlights()
            self.dialog.destroy()
            self.dialog = None
